"""Dashboard feature implementation using the caller’s live namespace."""
from __future__ import annotations

def _collect_services(_runtime_bindings, self) -> List[ServiceHealth]:
    """Collect service health status."""
    services = []
    from ..health_state import authoritative, read_status
    if authoritative(self.data_dir):
        snapshot = read_status(self.data_dir)
        for (name, group) in snapshot.get('groups', {}).items():
            completed = group.get('finished_at', 0)
            age = _runtime_bindings['datetime'].now(_runtime_bindings['timezone'].utc).timestamp() - completed
            checks = group.get('checks', [])
            failed = sum((not c.get('passed') for c in checks))
            services.append(_runtime_bindings['ServiceHealth'](name=name, status='unknown' if group.get('error') or age > group.get('period', 120) + 90 else 'error' if failed else 'running', last_run=_runtime_bindings['datetime'].fromtimestamp(completed, _runtime_bindings['timezone'].utc).isoformat(), last_run_ago='{}s ago'.format(max(0, int(age))), error_count=failed, log_file=str(self.data_dir / 'health-status.json')))
        return services
    service_dirs = {'shadow': self.data_dir / 'shadow', 'dispatch': self.data_dir / 'dispatch', 'conductor': self.data_dir / 'conductor', 'poller': self.karen_dir}
    for (name, svc_dir) in service_dirs.items():
        status_file = svc_dir / f'{name}-status.json' if name != 'poller' else svc_dir / 'poll_state.json'
        if name == 'conductor':
            status_file = svc_dir / 'conductor-status.json'
        elif name == 'dispatch':
            status_file = svc_dir / 'dispatch-status.json'
        elif name == 'shadow':
            status_file = svc_dir / 'shadow.log'
        svc = _runtime_bindings['ServiceHealth'](name=name, status='unknown', last_run='', last_run_ago='never', log_file=str(status_file))
        if svc_dir.is_dir():
            log_files = list(svc_dir.glob('*.jsonl')) + list(svc_dir.glob('*.log'))
            if log_files:
                newest = max(log_files, key=lambda p: p.stat().st_mtime)
                svc.log_file = str(newest)
                mtime = _runtime_bindings['datetime'].fromtimestamp(newest.stat().st_mtime, tz=_runtime_bindings['timezone'].utc)
                age = _runtime_bindings['datetime'].now(_runtime_bindings['timezone'].utc) - mtime
                svc.last_run = mtime.isoformat()
                if age < _runtime_bindings['timedelta'](minutes=5):
                    svc.last_run_ago = 'just now'
                    svc.status = 'running'
                elif age < _runtime_bindings['timedelta'](minutes=30):
                    svc.last_run_ago = f'{int(age.total_seconds() / 60)}m ago'
                    svc.status = 'running'
                elif age < _runtime_bindings['timedelta'](hours=2):
                    svc.last_run_ago = f'{int(age.total_seconds() / 3600)}h ago'
                    svc.status = 'stopped'
                else:
                    svc.last_run_ago = f'{int(age.total_seconds() / 86400)}d ago'
                    svc.status = 'stopped'
                lines = _runtime_bindings['_safe_read_lines'](newest, 50)
                svc.error_count = sum((1 for l in lines if 'error' in l.lower() or 'traceback' in l.lower()))
                if svc.error_count > 5:
                    svc.status = 'error'
        services.append(svc)
    return services

def _collect_guards(_runtime_bindings, self) -> GuardValues:
    """Collect current guard values."""
    guards = _runtime_bindings['GuardValues'](machine=self.machine, load=0.0, load_limit=10.0 if self.machine == 'mini' else 12.0)
    try:
        (load1, _, _) = _runtime_bindings['os'].getloadavg()
        guards.load = load1
    except OSError:
        pass
    if self.machine == 'mini':
        from ..guards import get_swap_pct
        guards.swap_pct = get_swap_pct()
        try:
            import shutil
            usage = shutil.disk_usage('/')
            guards.disk_gb = usage.free / 1024 ** 3
        except OSError:
            pass
    dispatch_status = _runtime_bindings['_safe_read_json'](self.data_dir / 'dispatch' / 'dispatch-status.json')
    if dispatch_status:
        guard_vals = dispatch_status.get('summary', {}).get('decisions', [{}])[0].get('guard_values', {})
        if guard_vals:
            guards.lanes_active = int(guard_vals.get('lanes', 0))
    guards.guards_passed = guards.load < guards.load_limit and (self.machine != 'mini' or guards.swap_pct < guards.swap_limit) and (self.machine != 'mini' or guards.disk_gb >= guards.disk_min) and (self.machine != 'nucbox' or guards.lanes_active < guards.lanes_max)
    return guards

def _collect_needs_jeff(_runtime_bindings, self) -> List[NeedsJeffItem]:
    """Collect NEEDS_JEFF items."""
    items = []
    from ..health_state import authoritative, read_status
    if authoritative(self.data_dir):
        for issue in read_status(self.data_dir).get('incidents', {}).values():
            if issue.get('notify') and issue.get('state') == 'firing':
                items.append(_runtime_bindings['NeedsJeffItem'](timestamp=_runtime_bindings['datetime'].fromtimestamp(issue['first_seen'], _runtime_bindings['timezone'].utc).isoformat(), issue='Health: {} / {}'.format(issue['machine'], issue['name']), details='Inspect unified health details and health logs.'))
    needs_jeff = self.data_dir / 'conductor' / 'NEEDS_JEFF.md'
    if not needs_jeff.is_file():
        return items
    content = needs_jeff.read_text()
    sections = content.split('## ')[1:]
    for section in sections[:10]:
        lines = section.strip().split('\n')
        if not lines:
            continue
        timestamp = lines[0].strip()
        details_lines = []
        for line in lines[1:]:
            if line.startswith('---'):
                break
            if line.strip():
                details_lines.append(line.strip())
        items.append(_runtime_bindings['NeedsJeffItem'](timestamp=timestamp, issue=details_lines[0] if details_lines else '', details='\n'.join(details_lines[1:])[:200] if len(details_lines) > 1 else ''))
    return items

def _collect_providers(_runtime_bindings, self) -> List[ProviderBudget]:
    """Collect provider budget usage."""
    providers = []
    budget_file = self.metrics_dir / 'budget-state.json'
    data = _runtime_bindings['_safe_read_json'](budget_file)
    if not data:
        data = {}
    provider_configs = {'codex': {'cap_pct': 75.0, 'status': 'ok'}, 'gemini': {'cap_pct': 100.0, 'status': 'ok'}, 'cursor': {'cap_pct': 100.0, 'status': 'ok'}, 'claude': {'cap_pct': 100.0, 'status': 'ok'}, 'minimax': {'cap_pct': 100.0, 'status': 'ok'}, 'grok': {'cap_pct': 0.0, 'status': 'conserve'}}
    for (name, config) in provider_configs.items():
        pdata = data.get(name, {})
        weekly_pct = _runtime_bindings['read_codex_quota']()['weekly_pct'] if name == 'codex' else pdata.get('weekly_pct', 0.0)
        status = config['status']
        if name != 'grok':
            if weekly_pct is None:
                status = 'unknown'
            elif weekly_pct >= config['cap_pct']:
                status = 'capped'
            elif weekly_pct >= config['cap_pct'] * 0.9:
                status = 'warning'
        providers.append(_runtime_bindings['ProviderBudget'](name=name, weekly_pct=weekly_pct, daily_spend_usd=None if name == 'codex' else pdata.get('daily_usd', 0.0), weekly_spend_usd=None if name == 'codex' else pdata.get('weekly_usd', 0.0), cap_pct=config['cap_pct'], status=status))
    return providers

def _collect_scorecard(_runtime_bindings, self) -> Optional[ScorecardMetrics]:
    """Collect scorecard metrics."""
    metrics_file = self.metrics_dir / 'karen-metrics.json'
    data = _runtime_bindings['_safe_read_json'](metrics_file)
    if not data:
        return None
    current = data.get('current_week', {})
    previous = data.get('previous_week', {})
    return _runtime_bindings['ScorecardMetrics'](first_try_pass_rate=current.get('first_try_pass_rate', 0.0), pre_llm_catch_rate=current.get('pre_llm_catch_rate', 0.0), fix_loop_success_rate=current.get('fix_loop_success_rate', 0.0), median_time_to_merge_hours=current.get('median_time_to_merge_hours', 0.0), total_reviews=current.get('total_reviews', 0), total_merges=current.get('total_merges', 0), stuck_pr_rate=current.get('stuck_pr_rate', 0.0), week_over_week={'first_try_pass_rate': previous.get('first_try_pass_rate', 0.0), 'fix_loop_success_rate': previous.get('fix_loop_success_rate', 0.0), 'median_time_to_merge_hours': previous.get('median_time_to_merge_hours', 0.0)})
