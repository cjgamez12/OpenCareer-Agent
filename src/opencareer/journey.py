"""Your job search as a flow chart (a Sankey diagram) that grows with every application.

Each application flows from "Applications" through the steps it actually reached
(recruiter screen, assessment, interview rounds, final round, offer) to where it stands now.
Drawn as plain SVG on your computer: no outside libraries, nothing sent anywhere.
"""
from html import escape

from opencareer import tracker

STEP_LABELS = {"recruiter_screen": "Recruiter screen", "assessment": "Assessment",
               "final_round": "Final round", "offer": "Offer"}
OUTCOME_LABELS = {"rejected": "Rejected", "ghosted": "Ghosted", "accepted": "Accepted"}
OUTCOMES = ["Ghosted", "Rejected", "Withdrew", "No reply yet", "In progress", "Declined", "Accepted"]
COLORS = {
    "Applications": "#1a7f5a", "Recruiter screen": "#d9822b", "Assessment": "#8a63d2",
    "Final round": "#2f9e8f", "Offer": "#1f9d55", "Accepted": "#2da44e", "Declined": "#cf4a6b",
    "Rejected": "#7c6fc4", "Ghosted": "#e0559a", "Withdrew": "#e3a72f", "No reply yet": "#8c959f",
    "In progress": "#3b82c4",
}
ROUND_COLORS = ["#e8590c", "#74b816", "#a0782c", "#495057", "#7048e8", "#1c7ed6"]


def _rank(node: str) -> int:
    """Left-to-right order of the steps. Outcomes all sit in the last column."""
    if node == "Applications":
        return 0
    if node == "Recruiter screen":
        return 1
    if node == "Assessment":
        return 2
    if node.startswith("Round "):
        return 2 + int(node.split()[1])
    return {"Final round": 50, "Offer": 60}.get(node, 100)


def path_for(events: list[tuple]) -> list[str]:
    """One application's events (happened_on, stage, round, note) -> its path through the chart."""
    path, rank, rounds, outcome = ["Applications"], 0, 0, None
    for _, stage, round_number, _ in events:
        if stage == "applied":
            continue
        if stage in OUTCOME_LABELS:
            outcome = OUTCOME_LABELS[stage]
            break
        if stage == "withdrawn":
            outcome = "Declined" if "Offer" in path else "Withdrew"
            break
        if stage == "interview":
            number = round_number or rounds + 1
            rounds = max(rounds, number)
            node = f"Round {min(number, 20)}"
        else:
            node = STEP_LABELS.get(stage)
        if node and _rank(node) > rank:                 # only forward: keeps the chart readable
            path.append(node)
            rank = _rank(node)
    path.append(outcome or ("No reply yet" if len(path) == 1 else "In progress"))
    return path


def flows(conn) -> dict[tuple[str, str], int]:
    """How many applications moved from each step to the next."""
    links: dict[tuple[str, str], int] = {}
    for _, _, url in tracker.list_applications(conn):
        path = path_for(tracker.history(conn, url))
        for a, b in zip(path, path[1:]):
            links[(a, b)] = links.get((a, b), 0) + 1
    return links


def _color(node: str) -> str:
    if node.startswith("Round "):
        return ROUND_COLORS[(int(node.split()[1]) - 1) % len(ROUND_COLORS)]
    return COLORS.get(node, "#6e7781")


def sankey_svg(links: dict[tuple[str, str], int], width: int = 1040, height: int = 400) -> str | None:
    """Draw the flow chart. Returns SVG markup, or None when there's nothing to draw yet."""
    if not links:
        return None
    nodes = {n for pair in links for n in pair}
    inflow = {n: sum(v for (a, b), v in links.items() if b == n) for n in nodes}
    outflow = {n: sum(v for (a, b), v in links.items() if a == n) for n in nodes}
    value = {n: max(inflow[n], outflow[n]) for n in nodes}

    ranks = sorted({_rank(n) for n in nodes})
    column = {n: ranks.index(_rank(n)) for n in nodes}
    columns = [[] for _ in ranks]
    order = {name: i for i, name in enumerate(OUTCOMES)}
    for n in sorted(nodes, key=lambda n: (order.get(n, -1), _rank(n), n)):
        columns[column[n]].append(n)

    node_w, top, left, right, gap = 14, 14, 8, 120, 34
    plot_h = height - top * 2
    first, last = columns[0], columns[-1]
    middle = columns[1:-1]
    scale = min(plot_h / max(sum(value[n] for n in first), 1),
                (plot_h - 20 - gap * (len(last) - 1)) / max(sum(value[n] for n in last), 1))   # room for the last label
    step = (width - left - right - node_w) / max(len(columns) - 1, 1)
    pos = {}
    y = top
    for n in first:                                     # Applications: the full height on the left
        h = max(value[n] * scale, 3)
        pos[n] = (left, y, h)
        y += h + gap
    y = top
    for n in last:                                      # where everything ended up, on the right
        h = max(value[n] * scale, 3)
        pos[n] = (left + (len(columns) - 1) * step, y, h)
        y += h + gap
    drop = min(16, plot_h * 0.3 / max(len(middle) - 1, 1))
    for i, col in enumerate(middle, start=1):           # the steps: a staircase across the lower half
        y = top + plot_h * 0.56 + (i - 1) * drop
        for n in col:
            h = max(value[n] * scale, 3)
            y = min(y, height - top - 30 - h)          # leave room for the label underneath
            pos[n] = (left + i * step, y, h)
            y += h + 34

    parts = []
    out_used = {n: 0.0 for n in nodes}
    in_used = {n: 0.0 for n in nodes}
    for (a, b), count in sorted(links.items(), key=lambda item: (pos[item[0][0]][1], pos[item[0][1]][1])):
        h = max(count * scale, 1.5)
        x0, y0 = pos[a][0] + node_w, pos[a][1] + out_used[a]
        x1, y1 = pos[b][0], pos[b][1] + in_used[b]
        out_used[a] += h
        in_used[b] += h
        xm = (x0 + x1) / 2
        d = (f"M{x0:.1f},{y0:.1f} C{xm:.1f},{y0:.1f} {xm:.1f},{y1:.1f} {x1:.1f},{y1:.1f} "
             f"L{x1:.1f},{y1 + h:.1f} C{xm:.1f},{y1 + h:.1f} {xm:.1f},{y0 + h:.1f} {x0:.1f},{y0 + h:.1f} Z")
        parts.append(f'<path d="{d}" fill="{_color(b)}" fill-opacity="0.45">'
                     f'<title>{escape(a)} → {escape(b)}: {count}</title></path>')
    middle_nodes = {n for col in middle for n in col}
    for n, (x, y, h) in pos.items():
        parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{node_w}" height="{h:.1f}" fill="{_color(n)}" rx="2">'
                     f'<title>{escape(n)}: {value[n]}</title></rect>')
        if n in middle_nodes:                           # label under the step
            tx, ty = x, y + h + 15
        else:                                           # label beside the bar
            tx, ty = x + node_w + 6, y + h / 2 + 2
        parts.append(f'<text x="{tx:.1f}" y="{ty:.1f}" font-size="16" font-weight="600" fill="currentColor">{value[n]}</text>'
                     f'<text x="{tx:.1f}" y="{ty + 13:.1f}" font-size="11" fill="currentColor" opacity="0.75">{escape(n)}</text>')
    total = value.get("Applications", 0)
    summary = f"Job search flow: {total} applications. " + ", ".join(
        f"{n} {value[n]}" for n in OUTCOMES if n in value)
    return (f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="{escape(summary)}" '
            f'style="font-family:inherit;color:var(--ink);max-height:{height}px">{"".join(parts)}</svg>')


def chart(conn) -> str | None:
    return sankey_svg(flows(conn))


def headline(links: dict[tuple[str, str], int]) -> str:
    """A one-line summary, e.g. '186 applications · 19 reached an interview · 3 offers'."""
    if not links:
        return ""
    total = sum(v for (a, _), v in links.items() if a == "Applications")
    reached = {b for (_, b) in links}
    interviewed = sum(v for (a, b), v in links.items()
                      if a in ("Applications", "Recruiter screen", "Assessment")
                      and (b.startswith("Round") or b in ("Final round", "Offer")))
    offers = sum(v for (_, b), v in links.items() if b == "Offer")
    parts = [f"{total} application{'s' if total != 1 else ''}"]
    if interviewed:
        parts.append(f"{interviewed} reached an interview ({interviewed * 100 // total}%)")
    if "Offer" in reached:
        parts.append(f"{offers} offer{'s' if offers != 1 else ''}")
    return " · ".join(parts)
