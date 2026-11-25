(function () {
  var state = { payload: null, kind: 'bars', run: null, approved: false };
  var $ = function (id) { return document.getElementById(id); };
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function setNotice(text) { $('notice').textContent = text || ''; }
  function paintValidity(run) {
    if (!run) return;
    var withdrawn = run.status === 'WITHDRAWN' || run.withdrawn === true || run.effective === false;
    var line = $('validity-line');
    var badge = $('validity-state');
    var reason = $('withdraw-reason');
    if (line && badge) {
      line.hidden = false;
      if (withdrawn) {
        badge.textContent = '已失效 · 已撤回';
        badge.setAttribute('data-v', 'REJECT');
      } else if (run.status === 'APPROVED') {
        badge.textContent = '有效 · 已签发';
        badge.setAttribute('data-v', 'PASS');
      } else if (run.status === 'HELD') {
        badge.textContent = '暂缓签发';
        badge.setAttribute('data-v', 'REVIEW');
      } else {
        badge.textContent = '草稿 · 尚未签发';
        badge.setAttribute('data-v', 'REVIEW');
      }
    }
    if (reason) {
      var text = run.withdrawal_reason || run.withdraw_reason || run.reason || '';
      if (withdrawn && text) {
        reason.hidden = false;
        reason.textContent = '撤回原因：' + text;
      } else {
        reason.hidden = true;
        reason.textContent = '';
      }
    }
    if ($('verify-evidence')) $('verify-evidence').disabled = !run.run_id;
    var checksumLine = $('checksum-line');
    if (checksumLine) {
      var digest = run.evidence_sha256 || run.artifact_sha256 || '';
      if (digest) {
        checksumLine.hidden = false;
        checksumLine.textContent = '原签发证据 SHA-256：' + digest;
      } else {
        checksumLine.hidden = true;
        checksumLine.textContent = '';
      }
    }
    if ($('download')) {
      var downloadable = run.status === 'APPROVED' && run.effective !== false;
      $('download').hidden = !downloadable;
      if (downloadable) $('download').href = '/api/runs/' + run.run_id + '/evidence';
    }
    if (withdrawn) {
      $('sign').disabled = true;
      $('hold').disabled = true;
    }
  }
  async function refreshWithdrawals() {
    try {
      var records = await request('/api/withdrawals');
      var list = $('withdraw-list');
      if (!list) return;
      if (!Array.isArray(records) || !records.length) {
        list.innerHTML = '<li class="empty">暂无撤回记录</li>';
        return;
      }
      list.innerHTML = records.map(function (item) {
        var runs = (item.run_ids || item.affected_run_ids || []).length;
        return '<li><span>' + esc(item.sample_id) + '</span>' +
          '<span>' + runs + ' 个批次失效</span><code>' + esc(item.reason || '') + '</code></li>';
      }).join('');
    } catch (error) { /* 工作台保留主流程，不打断 */ }
  }
  async function invalidateSample() {
    var sampleId = ($('spot-sample').value || '').trim();
    if (!sampleId) { setNotice('请填写要确认无效的水样编号'); return; }
    try {
      setNotice('');
      $('spot-invalidate').disabled = true;
      var payload = await request('/api/samples/' + encodeURIComponent(sampleId) + '/invalidate', {
        method: 'POST',
        body: JSON.stringify({reason: $('spot-reason').value || ''})
      });
      var affected = (payload.affected_run_ids || []).length;
      setNotice('已确认 ' + sampleId + ' 无效，' + affected + ' 个使用它的批次失效');
      await refreshWithdrawals();
      if (state.run) {
        var run = await request('/api/runs/' + state.run.run_id);
        state.run = run;
        state.approved = run.status === 'APPROVED' && run.effective !== false;
        $('run-status').textContent = run.status + ' · rev ' + run.revision +
          (run.effective === false ? ' · 已失效' : '');
        $('revision').textContent = 'rev ' + run.revision;
        paintValidity(run);
        render(run);
      }
    } catch (error) { setNotice(error.message); }
    finally { $('spot-invalidate').disabled = false; }
  }
  async function verifyEvidence() {
    if (!state.run) return;
    var out = $('verify-result');
    try {
      var result = await request('/api/runs/' + state.run.run_id + '/verify', {
        method: 'POST', body: JSON.stringify({include_artifact: false})
      });
      if (out) {
        if (result.checksum_valid) {
          out.textContent = '原签发证据与校验值一致（SHA-256 核验通过）；批次当前效力：' +
            (result.effective === false ? '已失效/撤回' : '有效');
          out.className = 'hint check-ok';
        } else if (!result.actual_sha256) {
          out.textContent = '暂无可复核的原签发证据（签发后生成并留存快照）';
          out.className = 'hint';
        } else {
          out.textContent = '校验值不一致，原签发证据可能已被改动';
          out.className = 'hint check-bad';
        }
      }
    } catch (error) {
      if (out) { out.textContent = '复核失败：' + error.message; out.className = 'hint check-bad'; }
    }
  }
  function renderIssues(data) {
    $('issue-count').textContent = data.issues.length + ' 条';
    $('issue-list').innerHTML = data.issues.length ? data.issues.map(function (item) {
      return '<li><span>' + esc(item.line || '-') + '</span><span>' + esc(item.key || '无主键') + '</span><code>' + esc(item.code) + '</code></li>';
    }).join('') : '<li class="empty">暂无异常</li>';
  }
  function renderBreakdown(items) {
    if (!items || !items.length) return '';
    return '<details><summary>' + items.length + ' 项</summary><ul>' + items.map(function (item) {
      return '<li>' + esc(item) + '</li>';
    }).join('') + '</ul></details>';
  }
  function renderRows(data) {
    var labels = Object.assign({label: '记录'}, state.resultColumns);
    var source = data.row_kind === 'source';
    var hasBreakdown = data.rows.some(function (row) { return row.breakdown && row.breakdown.length; });
    var keys = source ? state.inputColumns : Object.keys(labels).filter(function (key) {
      return data.rows.some(function (row) { return Object.hasOwn(row, key); });
    });
    $('row-count').textContent = source ? data.rows.length + ' 行预览' :
      data.summary.PASS + ' 通过 / ' + data.summary.REVIEW + ' 复核 / ' + data.summary.REJECT + ' 阻断';
    var tail = source ? '' : '<th>结论</th>' + (hasBreakdown ? '<th>计算分项</th>' : '');
    $('row-head').innerHTML = '<tr>' + keys.map(function (key) { return '<th>' + esc(labels[key] || key) + '</th>'; }).join('') + tail + '</tr>';
    $('row-body').innerHTML = data.rows.length ? data.rows.map(function (row) {
      var values = keys.map(function (key) { return '<td class="' + (typeof row[key] === 'number' ? 'num' : '') + '">' + esc(row[key]) + '</td>'; }).join('');
      var verdict = source ? '' : '<td><span class="verdict" data-v="' + esc(row.verdict) + '">' + esc(row.verdict || '') + '</span><br><small>' + esc(row.reason || '') + '</small></td>';
      var breakdown = hasBreakdown ? '<td>' + renderBreakdown(row.breakdown) + '</td>' : '';
      return '<tr>' + values + verdict + breakdown + '</tr>';
    }).join('') : '<tr><td class="empty" colspan="' + (keys.length + (source ? 0 : 1)) + '">没有可展示条目</td></tr>';
  }
  function renderLedger(data) {
    if (!data.ledger) { $('ledger-list').innerHTML = '<p class="empty">运行对账后显示</p>'; return; }
    var values = data.ledger;
    $('ledger-list').innerHTML = [
      ['来源行', values.counts.input], ['采信行', values.counts.accepted], ['隔离行', values.counts.isolated],
      ['精确重复', values.counts.duplicates], ['忽略行', values.counts.ignored], ['计算条目', values.counts.computed]
    ].map(function (row) { return '<div class="ledger-row"><span>' + row[0] + '</span><strong>' + row[1] + '</strong></div>'; }).join('') +
      '<div class="ledger-row"><span>' + esc(values.metric_field) + '</span><strong>' + esc(values.metric_total) + '</strong></div>' +
      values.checks.map(function (check) { return '<div class="ledger-row"><span>' + esc(check.label) + '</span><strong class="' + (check.ok ? 'check-ok' : 'check-bad') + '">' + (check.ok ? '通过' : '异常') + '</strong></div>'; }).join('');
  }
  function render(data) {
    state.payload = data;
    $('chart-label').textContent = Object.values(state.resultColumns)[0];
    $('m-input').textContent = data.counts.input;
    $('m-accepted').textContent = data.counts.accepted;
    $('m-isolated').textContent = data.counts.isolated + data.counts.duplicates;
    $('m-rejected').textContent = data.summary.REJECT;
    renderRows(data);
    renderIssues(data);
    renderLedger(data);
    window.renderChart($('chart'), data.chart && data.chart.length ? (state.kind || 'bars') : 'bars', data.chart || []);
  }
  function stageButtons(stages) {
    $('stage-nav').innerHTML = stages.map(function (stage, index) {
      return '<button type="button" data-stage="' + stage.id + '" aria-pressed="' + (index === 0 ? 'true' : 'false') + '">' + esc(stage.name) + '</button>';
    }).join('');
    Array.from(document.querySelectorAll('[data-stage]')).forEach(function (button) {
      button.addEventListener('click', function () {
        document.querySelectorAll('[data-stage]').forEach(function (other) { other.setAttribute('aria-pressed', String(other === button)); });
        loadStage(button.getAttribute('data-stage'));
      });
    });
  }
  async function loadStage(stage) {
    try {
      setNotice('');
      document.body.setAttribute('aria-busy', 'true');
      var payload = await request('/api/stages/' + stage, {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      render(payload);
    } catch (error) { setNotice(error.message); }
    finally { document.body.removeAttribute('aria-busy'); }
  }
  async function runBatch() {
    try {
      setNotice('');
      $('run').disabled = true;
      var run = await request('/api/runs', {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      state.run = run;
      state.approved = run.status === 'APPROVED' && run.effective !== false;
      render(run);
      $('run-status').textContent = run.status + ' · rev ' + run.revision +
        (run.effective === false ? ' · 已失效' : '');
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = run.summary.REJECT > 0 || state.approved || run.status === 'WITHDRAWN';
      $('hold').disabled = state.approved || run.status === 'WITHDRAWN';
      $('download').hidden = !state.approved;
      if (state.approved) $('download').href = '/api/runs/' + run.run_id + '/evidence';
      paintValidity(run);
    } catch (error) { setNotice(error.message); }
    finally { $('run').disabled = false; }
  }
  async function review(decision) {
    if (!state.run) return;
    try {
      var run = await request('/api/runs/' + state.run.run_id + '/review', {
        method: 'POST',
        body: JSON.stringify({decision: decision, comment: $('comment').value, expected_revision: state.run.revision})
      });
      state.run = run;
      state.approved = run.status === 'APPROVED' && run.effective !== false;
      $('run-status').textContent = run.status + ' · rev ' + run.revision +
        (run.effective === false ? ' · 已失效' : '');
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = true;
      $('hold').disabled = true;
      $('download').hidden = !state.approved;
      if (state.approved) $('download').href = '/api/runs/' + run.run_id + '/evidence';
      paintValidity(run);
    } catch (error) { setNotice(error.message); }
  }
  async function boot() {
    try {
      var catalog = await request('/api/catalog');
      state.kind = catalog.chart;
      state.resultColumns = catalog.result_columns;
      state.inputColumns = catalog.columns;
      stageButtons(catalog.stages.slice(0, 5));
      document.querySelector('.rail-note').textContent = catalog.code + ' · ' + catalog.stages[6].name;
      await loadStage('intake');
    } catch (error) { setNotice(error.message); }
    $('run').addEventListener('click', runBatch);
    $('sign').addEventListener('click', function () { review('APPROVED'); });
    $('hold').addEventListener('click', function () { review('HELD'); });
    if ($('spot-invalidate')) $('spot-invalidate').addEventListener('click', invalidateSample);
    if ($('spot-refresh')) $('spot-refresh').addEventListener('click', refreshWithdrawals);
    if ($('verify-evidence')) $('verify-evidence').addEventListener('click', verifyEvidence);
    refreshWithdrawals();
    $('profile').addEventListener('change', function () {
      state.run = null; state.approved = false;
      $('sign').disabled = true; $('hold').disabled = true; $('download').hidden = true;
      if ($('verify-evidence')) $('verify-evidence').disabled = true;
      var line = $('validity-line'); if (line) line.hidden = true;
      var reason = $('withdraw-reason'); if (reason) { reason.hidden = true; reason.textContent = ''; }
      $('run-status').textContent = '未执行批次'; $('revision').textContent = 'rev 0';
      loadStage('intake');
    });
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
