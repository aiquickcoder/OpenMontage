"""When a channel posts (pure logic, tested).

A channel has a target of posts a day (per_day — measured from the reference channels, research.freq) spread over the
day by an hour profile (hours: 24 weights, the reference channels' own posts by hour, MSK). By any moment of the day
the channel «owes» per_day × (profile share of the day so far); a post goes out while it owes more than it has
published, and never closer than min_gap_min to the previous one. Quiet hours (quiet_from..quiet_to, MSK): only
urgent posts. The first day of live publishing runs at `ramp` (0.5) of the target (owner: «первые сутки — половина»).

Urgent (the judge's «breaking» with a high score) skips the owed count but keeps a short gap — the owner's 3–5 minutes
from a first report to our post.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))
URGENT_GAP_MIN = 3


def flat_profile(quiet_from: int = 1, quiet_to: int = 7) -> list[float]:
    return [0.15 if in_quiet(h, quiet_from, quiet_to) else 1.0 for h in range(24)]


def in_quiet(hour: int, quiet_from: int, quiet_to: int) -> bool:
    if quiet_from == quiet_to:
        return False
    if quiet_from < quiet_to:
        return quiet_from <= hour < quiet_to
    return hour >= quiet_from or hour < quiet_to


def day_share(now: datetime, profile: list[float]) -> float:
    """Share of the day's posts that falls before `now` (MSK)."""
    t = now.astimezone(MSK)
    prof = profile if len(profile) == 24 and sum(profile) > 0 else [1.0] * 24
    done = sum(prof[:t.hour]) + prof[t.hour] * (t.minute + t.second / 60) / 60
    return done / sum(prof)


def factor(now: datetime, live_since: float, ramp: float) -> float:
    """The first 24 hours of live publishing run at `ramp` of the target."""
    if live_since and now.timestamp() - live_since < 86400:
        return ramp
    return 1.0


def owed(now: datetime, per_day: float, profile: list[float], live_since: float = 0, ramp: float = 0.5,
         since: float = 0) -> int:
    """Posts due by `now` today. A channel switched on (or to live) during the day owes only from then on — no burst
    of the morning's posts at once."""
    share = day_share(now, profile)
    if since and since > day_start(now).timestamp():
        share -= day_share(datetime.fromtimestamp(since, MSK), profile)
    return int(per_day * factor(now, live_since, ramp) * max(0.0, share) + 0.5)


def day_start(now: datetime) -> datetime:
    t = now.astimezone(MSK)
    return t.replace(hour=0, minute=0, second=0, microsecond=0)


def may_post(now: datetime, ch: dict, published_today: int, last_post_ts: float | None, urgent: bool = False,
             burst: int = 0) -> tuple[bool, str]:
    """(yes/no, why) for the channel row `ch` at `now`. burst: a fresh strong story may run that many posts ahead
    of the owed count (to be out before the others), the gap still holds."""
    if ch.get("paused"):
        return False, "канал на паузе"
    gap_min = (now.timestamp() - last_post_ts) / 60 if last_post_ts else 1e9
    hour = now.astimezone(MSK).hour
    if urgent:
        if gap_min < URGENT_GAP_MIN:
            return False, f"срочное: ждём {URGENT_GAP_MIN} мин после прошлого поста"
        return True, "срочное"
    if in_quiet(hour, int(ch.get("quiet_from", 1)), int(ch.get("quiet_to", 7))):
        return False, "тихие часы"
    if gap_min < float(ch.get("min_gap_min", 10)):
        return False, f"интервал {ch.get('min_gap_min')} мин"
    need = owed(now, float(ch.get("per_day", 20)), ch.get("hours") or flat_profile(), float(ch.get("live_since") or 0),
                float(ch.get("ramp") or 0.5), float(ch.get("since") or 0))
    if published_today >= need + burst:
        return False, f"по графику: {published_today} из {need} к этому часу"
    if published_today >= need:
        return True, f"свежее раньше графика: {published_today} из {need}+{burst}"
    return True, f"по графику: {published_today} из {need}"


def profile_from(by_hour_lists: list[list[float]], quiet_from: int = 1, quiet_to: int = 7, floor: float = 0.05) -> list[float]:
    """The average hour profile of reference channels (each a 24-list of posts per hour), normalised to sum 24,
    quiet hours kept at their own (small) weight but at least `floor`."""
    prof = [0.0] * 24
    for hl in by_hour_lists:
        s = sum(hl) or 1
        for h in range(24):
            prof[h] += hl[h] / s
    total = sum(prof) or 1
    out = [max(floor, p / total * 24) for p in prof]
    return [round(x, 3) for x in out]
