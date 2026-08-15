/* ==========================================================================
   FOOUND — verdicts. The owner's hands on the edition.
   ==========================================================================
   Signed out, this file does nothing visible: the page remains the public
   showroom. Signed in (magic link), each role gains two quiet verbs — PASS
   and APPLIED — with immediate settled state and UNDO, written directly to
   the owner's private store. Validation is the database's job (RLS, checks);
   this file's job is honesty: the UI only shows "settled" after the row
   truly exists.

   PASS   — out of active consideration. Optional one-tap reason.
   APPLIED — moved forward into pursuit. Never carries a reason.
   UNDO   — retraction, never deletion. Works now, after reload, on any
            signed-in device, any later day.
   ========================================================================== */
(function () {
  "use strict";

  var CFG = window.FOOUND_CFG || {};
  /* "other" is deliberately neutral: it removes the role and teaches nothing */
  var REASONS = ["seniority", "function", "compensation", "location", "company", "scope", "other"];
  var sb = null, agentId = null, authed = false;
  var active = {};          /* role_key -> signal row (state=active) */

  /* ------------------------------------------------------------- helpers */
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function el(tag, cls, text) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function snapshotFor(item, key) {
    var d = function (a) { return item.getAttribute("data-" + a) || ""; };
    var fit = parseInt(d("fit"), 10);
    return {
      version: 1, role_key: key,
      title: d("title"), company: d("company"),
      location: d("location"), url: d("url"),
      fit: isNaN(fit) ? null : fit,
      why: d("why"), pause: d("pause"),
      edition_date: CFG.edition || ""
    };
  }

  /* -------------------------------------------------------------- writes */
  var inflight = {};
  function writeSignal(key, payload) {
    if (inflight[key]) return inflight[key];
    var p = sb.from("signals").insert(payload).select().single()
      .then(function (res) {
        if (res.error) {
          /* duplicate active signal = the state already exists = success */
          if (res.error.code === "23505") return refresh().then(function () { return active[key]; });
          throw res.error;
        }
        active[key] = res.data;
        return res.data;
      })
      .finally(function () { delete inflight[key]; });
    inflight[key] = p;
    return p;
  }

  function pass(item, key, reason) {
    return writeSignal(key, {
      agent_id: agentId, kind: "pass", role_key: key,
      reason: reason || null, snapshot: snapshotFor(item, key)
    });
  }

  function applied(item, key) {
    return writeSignal(key, {
      agent_id: agentId, kind: "applied", role_key: key,
      role_state: "applied", snapshot: snapshotFor(item, key)
    });
  }

  function undo(key) {
    var row = active[key];
    if (!row) return Promise.resolve();
    return sb.from("signals").update({ state: "retracted" }).eq("id", row.id)
      .then(function (res) {
        if (res.error) throw res.error;
        delete active[key];
      });
  }

  function refresh() {
    return sb.from("signals")
      .select("id,kind,role_key,reason,role_state,created_at")
      .eq("state", "active")
      .then(function (res) {
        if (res.error) throw res.error;
        active = {};
        (res.data || []).forEach(function (r) { active[r.role_key] = r; });
      });
  }

  /* ------------------------------------------------------ per-entry UI */
  function buildControls(item) {
    var key = item.getAttribute("data-key");
    var actions = $(".actions", item);
    if (!key || !actions || $(".vwrap", item)) return;

    var wrap = el("div", "vwrap");

    /* PASS verb joins the action row */
    var passBtn = el("button", "mark vpass", "Pass");
    passBtn.type = "button";
    actions.appendChild(passBtn);

    /* the reason row — appears only after PASS is tapped */
    var chips = el("div", "vchips");
    chips.appendChild(el("span", "vchips-label", "Because —"));
    REASONS.forEach(function (r) {
      var c = el("button", "vchip", r);
      c.type = "button";
      c.addEventListener("click", function (e) { e.stopPropagation(); commitPass(r); });
      chips.appendChild(c);
    });
    var just = el("button", "vchip vchip-just", "just pass");
    just.type = "button";
    just.addEventListener("click", function (e) { e.stopPropagation(); commitPass(null); });
    chips.appendChild(just);
    var never = el("button", "vchip vchip-cancel", "cancel");
    never.type = "button";
    never.addEventListener("click", function (e) { e.stopPropagation(); setState("open"); });
    chips.appendChild(never);

    /* the settled line */
    var state = el("div", "vstate");
    wrap.appendChild(chips);
    wrap.appendChild(state);
    actions.parentNode.insertBefore(wrap, actions.nextSibling);

    var applyBtn = $("button.mark:not(.vpass)", item);

    function setState(mode, info) {
      item.classList.remove("vpassed", "applied", "vsaving");
      chips.classList.remove("on");
      state.textContent = "";
      state.classList.remove("on");
      actions.classList.remove("vhidden");
      if (mode === "chips") {
        chips.classList.add("on");
      } else if (mode === "saving") {
        item.classList.add("vsaving");
        actions.classList.add("vhidden");
        state.classList.add("on");
        state.appendChild(el("span", "vword", "Saving…"));
      } else if (mode === "passed") {
        item.classList.add("vpassed");
        actions.classList.add("vhidden");
        state.classList.add("on");
        state.appendChild(el("span", "vword",
          "Passed" + (info && info.reason ? " · " + info.reason : "")
          + " — leaves the next edition"));
        state.appendChild(mkUndo());
      } else if (mode === "applied") {
        item.classList.add("applied");
        actions.classList.add("vhidden");
        state.classList.add("on");
        state.appendChild(el("span", "vword", "Applied — tracked"));
        state.appendChild(mkUndo());
      } else if (mode === "error") {
        state.classList.add("on");
        state.appendChild(el("span", "vword verr",
          "Didn’t save — check connection and try again"));
      }
      if (applyBtn) applyBtn.textContent = "Mark applied";
    }

    function mkUndo() {
      var u = el("button", "vundo", "Undo");
      u.type = "button";
      u.addEventListener("click", function (e) {
        e.stopPropagation();
        setState("saving");
        undo(key).then(function () { setState("open"); })
                 .catch(function () { syncFromStore(); });
      });
      return u;
    }

    function commitPass(reason) {
      setState("saving");
      pass(item, key, reason)
        .then(function (row) { setState("passed", row); })
        .catch(function () { setState("error"); });
    }

    passBtn.addEventListener("click", function (e) {
      e.stopPropagation();
      setState("chips");
    });

    /* APPLIED: replace the localStorage-only handler with the real store */
    if (applyBtn) {
      var fresh = applyBtn.cloneNode(true);       /* strips the legacy listener */
      applyBtn.parentNode.replaceChild(fresh, applyBtn);
      applyBtn = fresh;
      applyBtn.addEventListener("click", function (e) {
        e.stopPropagation();
        setState("saving");
        applied(item, key)
          .then(function () { setState("applied"); })
          .catch(function () { setState("error"); });
      });
    }

    function syncFromStore() {
      var row = active[key];
      if (!row) setState("open");
      else if (row.kind === "pass") setState("passed", row);
      else setState("applied");
    }
    item._foundSync = syncFromStore;
    syncFromStore();
  }

  function markAll() { $all(".item[data-key]").forEach(buildControls); }

  /* --------------------------------------------------- signed-in nameplate
     Signed out the masthead reads FOOUND. Signed in it reads
     FOOUND · № 001 · SIGNED IN — the page says whose it is, at a glance,
     without a new surface. Falls back to a quiet fixed mark if a page has
     no masthead. */
  function padNo(n) {
    n = String(n == null ? "" : n);
    while (n.length < 3) n = "0" + n;
    return n;
  }
  function showOwnerMark(agentNo) {
    if ($(".vowner")) return;
    var css = document.createElement("style");
    css.textContent =
      ".vowner{color:#6b6b6b;} .vowner b{color:#000;font-weight:inherit;}" +
      ".vowner-fixed{position:fixed;top:14px;right:16px;z-index:60;background:#fff;" +
      "font-family:ui-monospace,'SF Mono',Menlo,Consolas,'Liberation Mono',monospace;" +
      "font-size:10.5px;letter-spacing:.14em;text-transform:uppercase;padding:4px 0 4px 8px;}";
    document.head.appendChild(css);
    var mark = el("span", "vowner");
    mark.appendChild(document.createTextNode(" · "));
    var no = document.createElement("b");
    no.textContent = "№ " + padNo(agentNo);
    mark.appendChild(no);
    mark.appendChild(document.createTextNode(" · signed in"));
    var host = $(".mast .id");
    if (host) host.appendChild(mark);
    else { mark.className += " vowner-fixed"; document.body.appendChild(mark); }
  }

  /* ------------------------------------------------------- sign-in sheet */
  function buildSheet() {
    if ($("#vsheet")) return $("#vsheet");
    var s = el("div", null); s.id = "vsheet";
    var inner = el("div", "vsheet-inner");
    inner.appendChild(el("div", "vsheet-label", "FOOUND · owner"));
    var input = el("input"); input.type = "email";
    input.placeholder = "your@email.com"; input.autocomplete = "email";
    input.id = "vemail";
    var send = el("button", "vsend", "Send sign-in link");
    send.type = "button";
    var msg = el("div", "vsheet-msg", "");
    send.addEventListener("click", function () {
      var email = input.value.trim();
      if (!email) { msg.textContent = "Enter the email your FOOUND knows."; return; }
      send.disabled = true; msg.textContent = "Sending…";
      sb.auth.signInWithOtp({ email: email }).then(function (res) {
        send.disabled = false;
        msg.textContent = res.error
          ? "Couldn’t send — " + res.error.message
          : "Sent. Open the link from this device’s email.";
      });
    });
    inner.appendChild(input); inner.appendChild(send); inner.appendChild(msg);
    s.appendChild(inner);
    document.body.appendChild(s);
    return s;
  }

  /* ---------------------------------------------------------------- init */
  function activate(session) {
    var agentNo = null;
    return sb.from("agents").select("id,agent_no").limit(1).single()
      .then(function (res) {
        if (res.error || !res.data) throw (res.error || new Error("no agent"));
        agentId = res.data.id;
        agentNo = res.data.agent_no;
        return refresh();
      })
      .then(function () {
        authed = true;
        document.body.classList.add("owner");
        showOwnerMark(agentNo);
        var sheet = $("#vsheet"); if (sheet) sheet.remove();
        markAll();
      })
      .catch(function (e) {
        /* Signed in but no agent = not an owner. Stay a visitor, quietly. */
        if (window.console) console.log("[foound] session without agent:", e && e.message);
      });
  }

  function init() {
    if (!window.supabase || !CFG.url || !CFG.key) return;
    sb = window.supabase.createClient(CFG.url, CFG.key);

    sb.auth.getSession().then(function (res) {
      var session = res && res.data && res.data.session;
      if (session) activate(session);
      else if (location.search.indexOf("me") === 1 ||
               location.search.indexOf("?me") === 0) buildSheet();
    });

    /* magic link lands here; supabase-js parses the URL hash itself */
    sb.auth.onAuthStateChange(function (event, session) {
      if (event === "SIGNED_IN" && session && !authed) {
        if (history.replaceState && location.hash.indexOf("access_token") !== -1) {
          history.replaceState(null, "", location.pathname);
        }
        activate(session);
      }
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else { init(); }
})();
