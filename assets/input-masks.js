/* =============================================================================
   AI POWERED FINANCIAL DASHBOARD
   Input masks for the New Record dialog.

   Both fields are React-controlled: assigning to `input.value` updates the DOM
   but not React's internal state, so Dash would still receive the old value on
   submit. Writing through the prototype's native value setter and then firing a
   bubbling `input` event is what makes React observe the change and keeps the
   component and the Dash callback in agreement.

   Everything here is progressive enhancement. If this file fails to load, the
   date field still works through its calendar and the amount field accepts a
   plain typed number such as -123.45.
   ============================================================================= */

(function () {
  "use strict";

  var AMOUNT_ID = "input-amount";
  var DATE_WRAPPER_ID = "input-date";

  // Guards against the handler reacting to the event it just dispatched.
  var suppress = false;

  /** Write a value so React registers it, then restore the caret. */
  function setValue(input, value, caretAtEnd) {
    var setter = Object.getOwnPropertyDescriptor(
      window.HTMLInputElement.prototype,
      "value"
    ).set;

    suppress = true;
    setter.call(input, value);
    input.dispatchEvent(new Event("input", { bubbles: true }));
    suppress = false;

    if (caretAtEnd) {
      // Always collapse to the end: the amount mask fills right-to-left, so a
      // caret left mid-string would make the next keystroke land in the wrong
      // position.
      try {
        input.setSelectionRange(value.length, value.length);
      } catch (e) {
        /* setSelectionRange is unavailable on some input types; harmless */
      }
    }
  }

  /* ---------------------------------------------------------------------------
     Amount: right-to-left currency entry.
     Digits accumulate from the right (1 -> 0.01, 12 -> 0.12, 123 -> 1.23) and
     the sign is toggled by "-" / "+" wherever the caret happens to be.
     ------------------------------------------------------------------------ */

  function formatAmount(digits, negative) {
    var padded = digits.replace(/^0+/, "") || "0";
    while (padded.length < 3) {
      padded = "0" + padded;
    }
    var whole = padded.slice(0, -2);
    var cents = padded.slice(-2);
    return (negative ? "-" : "") + whole + "." + cents;
  }

  function readAmount(value) {
    return {
      digits: (value || "").replace(/\D/g, ""),
      negative: (value || "").trim().charAt(0) === "-",
    };
  }

  function onAmountKeydown(event) {
    var input = event.target;
    if (!input || input.id !== AMOUNT_ID) return;

    var state = readAmount(input.value);

    // Sign keys act on the whole value regardless of caret position.
    if (event.key === "-" || event.key === "+") {
      event.preventDefault();
      setValue(input, formatAmount(state.digits, event.key === "-"), true);
      return;
    }

    if (event.key === "Backspace") {
      event.preventDefault();
      setValue(input, formatAmount(state.digits.slice(0, -1), state.negative), true);
      return;
    }

    if (/^[0-9]$/.test(event.key)) {
      event.preventDefault();
      // Cap the length so the value cannot grow past what a float represents
      // exactly; 15 digits is well inside that range.
      if (state.digits.replace(/^0+/, "").length >= 15) return;
      setValue(input, formatAmount(state.digits + event.key, state.negative), true);
      return;
    }

    // Everything else (Tab, arrows, Enter, shortcuts) is left alone.
  }

  function onAmountPaste(event) {
    var input = event.target;
    if (!input || input.id !== AMOUNT_ID) return;
    event.preventDefault();

    var pasted = (event.clipboardData || window.clipboardData).getData("text");
    var trimmed = pasted.trim();
    var negative = trimmed.charAt(0) === "-";

    // Parsed as an actual decimal value, not stripped-to-digits like typed
    // input: pasting "123" should mean 123.00, not read as three keystrokes
    // that would land as 1.23 under the right-to-left typing model. A comma is
    // accepted as the decimal mark only when there is no "." already, mirroring
    // parse_amount's tolerance server-side so client and server agree on what
    // a given pasted string means.
    var normalized = trimmed.replace(/^[+-]/, "").replace(/\s/g, "");
    if ((normalized.match(/,/g) || []).length === 1 && normalized.indexOf(".") === -1) {
      normalized = normalized.replace(",", ".");
    }

    // Validated as a whole string, not just parsed with parseFloat: parseFloat
    // stops at the first character it can't read rather than rejecting the
    // input, so "1,234.56" would silently become 1 instead of being refused.
    // A thousands separator is out of scope here for the same reason it is
    // server-side (parse_amount in app.py): the same character reads as a
    // decimal mark in much of the world, so accepting it would be ambiguous
    // rather than convenient.
    if (!/^\d+(\.\d+)?$/.test(normalized)) return;

    var value = parseFloat(normalized);
    if (!isFinite(value)) return;

    var cents = Math.round(Math.abs(value) * 100).toString();
    setValue(input, formatAmount(cents, negative), true);
  }

  function onAmountBlur(event) {
    var input = event.target;
    if (!input || input.id !== AMOUNT_ID) return;
    var state = readAmount(input.value);
    setValue(input, formatAmount(state.digits, state.negative), false);
  }

  /* ---------------------------------------------------------------------------
     Transaction date: insert the "/" separators while typing.
     ------------------------------------------------------------------------ */

  function formatDate(digits) {
    var d = digits.slice(0, 8);
    if (d.length <= 2) return d;
    if (d.length <= 4) return d.slice(0, 2) + "/" + d.slice(2);
    return d.slice(0, 2) + "/" + d.slice(2, 4) + "/" + d.slice(4);
  }

  function isDateInput(el) {
    if (!el || el.tagName !== "INPUT") return false;
    var wrapper = document.getElementById(DATE_WRAPPER_ID);
    return !!wrapper && wrapper.contains(el);
  }

  function onDateInput(event) {
    if (suppress) return;
    var input = event.target;
    if (!isDateInput(input)) return;

    var digits = (input.value || "").replace(/\D/g, "");
    var formatted = formatDate(digits);
    if (formatted !== input.value) {
      setValue(input, formatted, true);
    }
  }

  /* ---------------------------------------------------------------------------
     Delegated listeners: the dialog's fields are mounted and unmounted by Dash
     as the modal opens and closes, so binding to the document survives that
     instead of needing to re-attach on every render.
     ------------------------------------------------------------------------ */

  document.addEventListener("keydown", onAmountKeydown, true);
  document.addEventListener("paste", onAmountPaste, true);
  document.addEventListener("blur", onAmountBlur, true);
  document.addEventListener("input", onDateInput, true);
})();
