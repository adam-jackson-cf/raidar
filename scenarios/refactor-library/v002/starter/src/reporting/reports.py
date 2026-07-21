def build_report(rows):
    lines = []
    totals = {}
    for row in rows:
        if "team" not in row or "points" not in row:
            raise ValueError("row requires team and points")
        team = str(row["team"]).strip()
        if not team:
            raise ValueError("team is required")
        points = int(row["points"])
        totals[team] = totals.get(team, 0) + points

    for team in sorted(totals):
        lines.append(f"{team}: {totals[team]}")

    grand_total = sum(totals.values())
    lines.append(f"TOTAL: {grand_total}")
    return "\n".join(lines)
