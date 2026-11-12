'use strict';
// Instructor spotlight: shows one instructor's photo and name with back/forward buttons.
// Each step sets the dashboard's "Selected Instructor" parameter, which filters the other sheets.
// Reads the instructor list (name, image url) from the summary data of the "Time by Instructor" sheet.
(function () {
  var PARAM_NAME = 'Selected Instructor';
  var SOURCE_SHEET = 'Time by Instructor';
  var instructors = [];
  var index = 0;
  var parameter = null;
  var diagnostics = [];

  window.onload = function () {
    renderMessage('Loading...');
    tableau.extensions.initializeAsync().then(load, function (err) {
      renderMessage('Init error: ' + describeError(err));
    });
  };

  function load() {
    var dashboard = tableau.extensions.dashboardContent.dashboard;
    dashboard.getParametersAsync().then(function (params) {
      parameter = params.filter(function (p) { return p.name === PARAM_NAME; })[0] || null;
      diagnostics.push('v2, ' + dashboard.worksheets.length + ' worksheets');
      return loadInstructors(dashboard.worksheets);
    }).then(function () {
      if (parameter && parameter.allowableValues && parameter.allowableValues.type === 'list') {
        var allowed = {};
        parameter.allowableValues.allowableValues.forEach(function (v) { allowed[String(v.value)] = true; });
        instructors = instructors.filter(function (i) { return allowed[i.name]; });
      }
      if (!instructors.length) { renderMessage('No instructors found. ' + diagnostics.join(' | ')); return; }
      if (parameter) {
        var current = String(parameter.currentValue.value);
        var at = instructors.findIndex(function (i) { return i.name === current; });
        if (at >= 0) index = at;
      }
      show();
    }).catch(function (err) {
      renderMessage('Data error: ' + describeError(err));
    });
  }

  function loadInstructors(sheets) {
    var ws = sheets.filter(function (w) { return w.name === SOURCE_SHEET; })[0];
    if (!ws) return Promise.reject(new Error('Sheet "' + SOURCE_SHEET + '" is not on this dashboard.'));
    return ws.getSummaryDataAsync({ ignoreSelection: true }).then(function (data) {
      var cols = data.columns.map(function (c) { return c.fieldName; });
      diagnostics.push(SOURCE_SHEET + ': columns [' + cols.join(', ') + '], ' + data.data.length + ' rows');
      var nameIdx = findCol(cols, /name/i);
      var imgIdx = findCol(cols, /image/i);
      if (nameIdx < 0) return;
      var seen = {};
      data.data.forEach(function (r) {
        var n = r[nameIdx].formattedValue;
        if (!n || n === 'Null' || n === 'No Instructor' || seen[n]) return;
        seen[n] = true;
        instructors.push({ name: n, image: imgIdx >= 0 ? r[imgIdx].formattedValue : '' });
      });
      instructors.sort(function (a, b) { return a.name.localeCompare(b.name); });
    });
  }

  function findCol(cols, re) {
    for (var k = 0; k < cols.length; k++) { if (re.test(cols[k])) return k; }
    return -1;
  }

  function step(delta) {
    index = (index + delta + instructors.length) % instructors.length;
    show();
    if (parameter) {
      parameter.changeValueAsync(instructors[index].name).catch(function (err) {
        renderMessage('Filter error: ' + describeError(err));
      });
    }
  }

  function show() {
    var inst = instructors[index];
    var root = clearRoot();
    var row = el('div', 'row');
    row.appendChild(button('◀', 'Previous instructor', function () { step(-1); }));
    row.appendChild(portrait(inst));
    row.appendChild(button('▶', 'Next instructor', function () { step(1); }));
    root.appendChild(row);
    root.appendChild(el('div', 'name', inst.name));
    root.appendChild(el('div', 'count', (index + 1) + ' of ' + instructors.length));
  }

  function portrait(inst) {
    if (inst.image && /^https?:\/\//.test(inst.image)) {
      var img = el('img', 'photo');
      img.alt = inst.name;
      img.onerror = function () { img.replaceWith(avatar(inst)); };
      img.src = inst.image;
      return img;
    }
    return avatar(inst);
  }

  function avatar(inst) {
    var initials = inst.name.split(/\s+/).map(function (p) { return p.charAt(0); }).join('').slice(0, 2).toUpperCase();
    return el('div', 'avatar', initials);
  }

  function button(label, title, onClick) {
    var b = el('button', null, label);
    b.title = title;
    b.setAttribute('aria-label', title);
    b.onclick = onClick;
    return b;
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text) e.textContent = text;
    return e;
  }

  function renderMessage(text) {
    var p = el('p', null, text);
    p.style.fontSize = '10px';
    p.style.overflow = 'auto';
    p.style.wordBreak = 'break-word';
    clearRoot().appendChild(p);
  }

  function clearRoot() {
    var root = document.getElementById('content');
    while (root.firstChild) root.removeChild(root.firstChild);
    return root;
  }

  function describeError(err) {
    return (err && err.message) ? err.message : String(err);
  }
})();
