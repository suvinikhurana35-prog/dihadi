"""Runs every morning (EventBridge Scheduler, 09:00 IST). For each fund,
reads the last few days of weather, records any heatwave or rain spell that
qualifies for a payout (a missed run is caught the next morning), and emails
a warning when tomorrow's forecast crosses the trigger."""
import checks
import store


def handler(event, context):
    done, failed, changed_total = 0, 0, 0
    for group in store.list_groups():
        try:
            new, _ = checks.run_check(group, past_days=max(3, group["days"] + 2))
            changed_total += len(new)
            done += 1
        except Exception as err:
            failed += 1
            print(f"check failed for {group.get('id')}: {err}")
    summary = {"checked": done, "failed": failed, "changedEvents": changed_total}
    print(summary)
    return summary
