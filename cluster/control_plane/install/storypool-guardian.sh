#!/bin/bash
# Reload any com.storypool.* LaunchAgent that is installed but not loaded (they've been dropping out after updates/reboots).
U=$(id -u); LOG=~/.local/share/storypool/guardian.log
for p in ~/Library/LaunchAgents/com.storypool.*.plist; do
  [ -f "$p" ] || continue
  l=$(basename "$p" .plist)
  [ "$l" = com.storypool.guardian ] && continue
  if ! launchctl print "gui/$U/$l" >/dev/null 2>&1; then
    if launchctl bootstrap "gui/$U" "$p" 2>>"$LOG"; then
      echo "$(date '+%F %T %Z') reloaded $l" >> "$LOG"
    else
      echo "$(date '+%F %T %Z') FAILED to reload $l" >> "$LOG"
    fi
  fi
done
