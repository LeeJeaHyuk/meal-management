"""Local statistical projections and offline charts with ratio-only public exports."""
import base64
from datetime import timedelta
from decimal import Decimal, localcontext
from html import escape
import json
import sys

from .catalog import data_path, iso_date, number

from .reporting import MACRO_FACTORS
PROFILE_FIELDS = {"equation", "age_years", "height_cm", "weight_kg", "activity_factor"}
METHOD_URL = "https://pubmed.ncbi.nlm.nih.gov/2305711/"


def load_profile(path):
    if path is None:
        return None
    if path == "-":
        text = sys.stdin.read(65537)
    else:
        source = data_path(path)
        if source.stat().st_size > 65536:
            raise ValueError("energy_profile_too_large")
        text = source.read_text(encoding="utf-8")
    if len(text.encode("utf-8")) > 65536:
        raise ValueError("energy_profile_too_large")
    return json.loads(text)


def estimate_expenditure(profile):
    if profile is None:
        return {"state": "missing_profile", "required_inputs": sorted(PROFILE_FIELDS)}
    required = PROFILE_FIELDS - {"activity_factor"}
    if not isinstance(profile, dict) or set(profile) - PROFILE_FIELDS or required - set(profile):
        raise ValueError("invalid_energy_profile")
    if profile["equation"] not in {"male", "female"}:
        raise ValueError("explicit_energy_equation_required")
    age = number(profile["age_years"], True)
    height = number(profile["height_cm"], True)
    weight = number(profile["weight_kg"], True)
    if age < 18:
        raise ValueError("adult_energy_profile_required")
    resting = 10 * weight + Decimal("6.25") * height - 5 * age
    resting += 5 if profile["equation"] == "male" else -161
    if resting <= 0:
        raise ValueError("invalid_energy_profile")
    factor = profile.get("activity_factor")
    daily = resting * number(factor, True) if factor is not None else None
    return {
        "state": "estimated" if daily is not None else "missing_activity_factor",
        "method": "mifflin_st_jeor_1990",
        "source": METHOD_URL,
        "resting_kcal_per_day": str(resting),
        "daily_kcal": str(daily) if daily is not None else None,
        "outside_original_age_range": not 19 <= age <= 78,
    }


def _percentage(value, total):
    if value is None or not total:
        return None
    with localcontext() as context:
        context.prec = 80
        return str((number(value) * 100 / total).quantize(Decimal("0.01")))


def _signed_number(value):
    text = str(value)
    return -number(text[1:]) if text.startswith("-") else number(value)


def _intake_change(current, previous):
    if current is None or previous is None:
        return None, None
    with localcontext() as context:
        context.prec = 80
        delta = number(current) - number(previous)
        percent = str((delta * 100 / number(previous)).quantize(Decimal("0.01"))) if number(previous) else None
        return str(delta), percent


def _daily_composition(observed):
    macro = observed.get("macronutrient_composition", {})
    energy = macro.get("energy_kcal", {})
    complete = all(key in energy for key in MACRO_FACTORS)
    total = sum((number(energy[key]) for key in MACRO_FACTORS), Decimal(0)) if complete else Decimal(0)
    return {
        **{key.removesuffix("_g") + "_percent": _percentage(energy.get(key), total) for key in MACRO_FACTORS},
        "composition_partial": bool(macro.get("incomplete_components") or macro.get("excluded_inputs")),
        "composition_estimated": bool(macro.get("estimated_components")),
    }


def build(report, profile=None, mode="private"):
    if mode not in {"private", "public"}:
        raise ValueError("invalid_statistics_mode")
    first, last = iso_date(report["start"]), iso_date(report["end"])
    length = (last - first).days + 1
    if length < 1 or length > 3660:
        raise ValueError("invalid_statistics_period")
    observations = {row["date"]: row for row in report["daily_observations"]}
    energy = estimate_expenditure(profile)
    days = []
    for offset in range(length):
        day = (first + timedelta(days=offset)).isoformat()
        observed = observations.get(day, {})
        confirmed = observed.get("nutrition_confirmed_partial", {}).get("energy_kcal")
        estimated = observed.get("nutrition_estimated_partial", {}).get("energy_kcal")
        values = [number(value) for value in (confirmed, estimated) if value is not None]
        days.append({
            "label": day,
            "intake": str(sum(values)) if values else None,
            "estimated_intake": str(number(estimated)) if estimated is not None else None,
            "expenditure": energy.get("daily_kcal"),
            "weight": observed.get("weight_daily_mean_kg"),
            **_daily_composition(observed),
        })
    for index, row in enumerate(days):
        previous = days[index - 1]["intake"] if index else None
        row["intake_change_kcal"], row["intake_change_percent"] = _intake_change(row["intake"], previous)
    macro = report.get("macronutrient_composition", {})
    macro_energy = macro.get("energy_kcal", {})
    complete = all(key in macro_energy for key in MACRO_FACTORS)
    total = sum((number(macro_energy[key]) for key in MACRO_FACTORS), Decimal(0)) if complete else Decimal(0)
    coverage = report.get("nutrition_coverage", {})
    excluded = ("records_without_nutrition", "missing_or_invalid_reference", "unreviewed_component", "unit_mismatch")
    management = report.get("management_statistics", {})
    result = {
        "schema_version": 1, "mode": "private", "days": days,
        "management_percent": {
            "recorded_days": _percentage(management.get("recorded_days"), Decimal(length)),
            "nutrition_linked_records": _percentage(management.get("nutrition_linked_meal_record_count"), number(management.get("meal_record_count", 0))),
        },
        "composition_percent": {key.removesuffix("_g"): _percentage(macro_energy.get(key), total) for key in MACRO_FACTORS},
        "composition_partial": bool(macro.get("incomplete_components") or any(coverage.get(key) for key in excluded)),
        "composition_estimated": bool(macro.get("estimated_components")),
        "energy_unit": "kcal", "weight_unit": "kg", "energy_estimate": energy,
        "source_report_hash": report.get("report_hash"),
        "coverage": report.get("nutrition_coverage", {}),
        "notes": [
            "섭취 열량은 영양정보가 연결된 기록의 부분합입니다.",
            "미기록·미측정 구간은 빈칸으로 표시합니다.",
            "소비 열량은 입력한 신체 정보와 활동계수에 따른 추정입니다.",
            "체중과 섭취 기록의 동행만으로 변화의 원인을 판단할 수 없습니다.",
            "전일 대비 변화는 인접한 두 날짜의 기록 부분합을 비교합니다. 첫날·전날 미기록·전날 0인 증감률은 미확인입니다.",
        ],
    }
    return public_view(result) if mode == "public" else result


def public_view(statistics):
    if statistics.get("mode") != "private":
        raise ValueError("private_statistics_required")
    days = statistics["days"]
    total = sum((number(row["intake"]) for row in days if row["intake"] is not None), Decimal(0))
    projected = []
    for offset, row in enumerate(days, 1):
        projected.append({
            "label": f"{offset}일차",
            "intake": _percentage(row["intake"], total),
            "estimated_intake": _percentage(row["estimated_intake"], total),
            "intake_change_percent": _intake_change(row["intake"], days[offset - 2]["intake"] if offset > 1 else None)[1],
            **{key.removesuffix("_g") + "_percent": row.get(key.removesuffix("_g") + "_percent") for key in MACRO_FACTORS},
            "composition_partial": bool(row.get("composition_partial")),
            "composition_estimated": bool(row.get("composition_estimated")),
        })
    return {
        "schema_version": 1, "mode": "public", "days": projected,
        "management_percent": {key: statistics.get("management_percent", {}).get(key) for key in ("recorded_days", "nutrition_linked_records")},
        "energy_unit": "percent",
        "composition_percent": {key.removesuffix("_g"): statistics["composition_percent"][key.removesuffix("_g")] for key in MACRO_FACTORS},
        "composition_partial": statistics["composition_partial"],
        "composition_estimated": statistics["composition_estimated"],
        "notes": [
            "날짜별 비중은 기간 내 기록된 섭취 열량의 합계를 100%로 환산했습니다.",
            "영양 구성비는 탄수화물·단백질·지방이 함께 확인된 항목의 환산 열량 기준입니다.",
            "환산계수는 탄수화물·단백질 4 kcal/g, 지방 9 kcal/g입니다.",
            "미기록 구간은 빈칸으로 표시합니다. 비율은 소수 둘째 자리까지 표시합니다.",
            "전일 증감률은 (당일 기록 열량 − 전날 기록 열량) / 전날 기록 열량 × 100입니다. 실제 섭취 전체의 변화는 아닙니다.",
            "첫날·전날 미기록·전날 0인 증감률은 미확인입니다. 날짜별 구성비도 계산 가능한 기록만 포함합니다.",
            "날짜별 구성비의 일부 제외·추정 정보 포함 여부는 날짜별 값 표에서 확인합니다.",
        ],
    }


def _chart(chart_id, title, days, series, unit, zero_origin, signed=False):
    parse = _signed_number if signed else number
    values = [float(parse(day[key])) for day in days for key, _, _ in series if day[key] is not None]
    if not values:
        return f'<section class="chart"><h2>{escape(title)}</h2><p class="empty">표시할 기록이 없습니다.</p></section>'
    low, high = min(values), max(values)
    padding = (high - low) / 10 or max(high / 20, 1)
    low = min(0, low - padding) if signed else (0 if zero_origin else max(0, low - padding))
    high = max(0, high + padding) if signed else high + padding
    width, height, left, top = 820, 230, 70, 24

    def x(index):
        return left + width * index / max(len(days) - 1, 1)

    def y(value):
        return top + height * (1 - (value - low) / (high - low))

    svg = [f'<svg viewBox="0 0 930 310" role="img" aria-labelledby="{chart_id}-title">',
           f'<title id="{chart_id}-title">{escape(title)} ({escape(unit)})</title>']
    if signed:
        svg.append(f'<line x1="{left}" y1="{y(0):.2f}" x2="{left + width}" y2="{y(0):.2f}" stroke="#9caea3" stroke-width="1.2"/>')
    for tick in range(5):
        value = low + (high - low) * tick / 4
        position = y(value)
        svg.append(f'<line class="grid" x1="{left}" y1="{position:.2f}" x2="{left + width}" y2="{position:.2f}"/>')
        svg.append(f'<text class="tick" x="{left - 10}" y="{position + 4:.2f}" text-anchor="end">{value:.1f}</text>')
    ticks = sorted(set(range(0, len(days), max(1, (len(days) - 1) // 4))) | {len(days) - 1})
    for tick in ticks:
        svg.append(f'<text class="tick" x="{x(tick):.2f}" y="285" text-anchor="middle">{escape(days[tick]["label"])}</text>')
    for key, label, color in series:
        points = []
        segments = []
        for offset, day in enumerate(days):
            value = day[key]
            if value is None:
                if points:
                    segments.append(points)
                    points = []
                continue
            numeric = float(parse(value))
            points.append(f"{x(offset):.2f},{y(numeric):.2f}")
            svg.append(f'<circle cx="{x(offset):.2f}" cy="{y(numeric):.2f}" r="3.5" fill="{color}">'
                       f'<title>{escape(day["label"])} · {escape(label)} {numeric:.2f} {escape(unit)}</title></circle>')
        if points:
            segments.append(points)
        dashed = ' stroke-dasharray="7 5"' if key != "intake" and key != "weight" else ""
        for segment in segments:
            svg.append(f'<polyline points="{" ".join(segment)}" fill="none" stroke="{color}" stroke-width="2.5"{dashed}/>')
    svg.append("</svg>")
    legend = "".join(f'<span><i style="background:{color}"></i>{escape(label)}</span>' for _, label, color in series)
    return f'<section class="chart"><h2>{escape(title)}</h2><div class="legend">{legend}</div>{"".join(svg)}</section>'


def _composition(statistics):
    values = statistics["composition_percent"]
    labels = {"carbohydrate": ("탄수화물", "#176b5b"),
              "protein": ("단백질", "#536a9d"), "fat": ("지방", "#b78120")}
    if not any(value is not None for value in values.values()):
        return '<section class="chart"><h2>섭취 영양 구성비</h2><p class="empty">탄수화물·단백질·지방이 함께 확인된 항목이 필요합니다.</p></section>'
    segments, legend = [], []
    for key, (label, color) in labels.items():
        value = float(number(values[key]))
        segments.append(f'<span title="{label} {value:.2f}%" style="width:{value:.2f}%;background:{color}"></span>')
        legend.append(f'<span><i style="background:{color}"></i>{label} {value:.2f}%</span>')
    detail = "계산 가능한 기록의 탄수화물·단백질·지방을 4·4·9 kcal/g로 환산했습니다."
    if statistics["composition_partial"]:
        detail += " 영양정보가 불완전한 항목은 구성비 계산에서 제외했습니다."
    if statistics["composition_estimated"]:
        detail += " 추정 영양정보가 포함돼 있습니다."
    return f'<section class="chart"><h2>섭취 영양 구성비</h2><div class="composition">{"".join(segments)}</div><div class="legend">{"".join(legend)}</div><p class="meta">{detail}</p></section>'




def render_public_svg(statistics):
    """Blog image from fixed ratio fields only; never embeds daily/private payloads."""
    if statistics.get("mode") == "private":
        statistics = public_view(statistics)
    if statistics.get("mode") != "public":
        raise ValueError("public_statistics_required")

    def ratio(value):
        if value is None:
            return None
        parsed = number(value)
        if not 0 <= parsed <= 100:
            raise ValueError("invalid_public_percentage")
        return float(parsed)

    management = statistics.get("management_percent", {})
    metrics = (
        ("기록일 비율", ratio(management.get("recorded_days")),
         "선택한 기간 중 식사 기록이 있는 날의 비율"),
        ("영양정보 연결률", ratio(management.get("nutrition_linked_records")),
         "식사 기록 중 검토된 영양정보가 연결된 비율"),
    )
    colors = ("#176b5b", "#536a9d")
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 760" width="1120" height="760" role="img" aria-labelledby="blog-title blog-description">',
        '<title id="blog-title">식사 기록 관리 · 공개용 비율 요약</title>',
        '<desc id="blog-description">기록일 비율, 영양정보 연결률, 삼대영양소 열량 구성비를 표시합니다. 개인 상세값과 날짜별 자료는 포함하지 않습니다.</desc>',
        '<style>text{font-family:"Noto Sans KR","Malgun Gothic",sans-serif;fill:#24342f}.muted{fill:#60746a;font-size:16px}.label{font-size:22px;font-weight:600}.metric{font-size:48px;font-weight:700}.small{font-size:14px;fill:#60746a}</style>',
        '<rect width="1120" height="760" rx="24" fill="#f4f6f5"/>',
        '<text x="48" y="53" class="small" letter-spacing="2">MEAL JOURNAL / PUBLIC SUMMARY</text>',
        '<text x="48" y="99" font-size="32" font-weight="700">기록하고, 근거를 연결하고, 돌아보기</text>',
        '<text x="48" y="132" class="muted">상세 수치 대신 관리 과정과 구성비로 보는 식사 기록</text>',
    ]
    for index, (title, value, explanation) in enumerate(metrics):
        x = 48 + index * 520
        color = colors[index]
        shown = "미확인" if value is None else f"{value:.2f}%"
        parts.extend([
            f'<rect x="{x}" y="166" width="504" height="196" rx="16" fill="white" stroke="#dce5df"/>',
            f'<text x="{x + 24}" y="207" class="label">{title}</text>',
            f'<text x="{x + 24}" y="266" class="metric">{shown}</text>',
            f'<rect x="{x + 24}" y="285" width="456" height="10" rx="5" fill="#e8eeeb"/>',
        ])
        if value is not None:
            parts.append(f'<rect x="{x + 24}" y="285" width="{456 * value / 100:.2f}" height="10" rx="5" fill="{color}"/>')
        parts.append(f'<text x="{x + 24}" y="330" class="small">{explanation}</text>')
    parts.extend([
        '<rect x="48" y="382" width="1024" height="228" rx="16" fill="white" stroke="#dce5df"/>',
        '<text x="72" y="423" class="label">섭취 영양 구성비</text>',
    ])
    labels = (("carbohydrate", "탄수화물", "#176b5b"),
              ("protein", "단백질", "#536a9d"), ("fat", "지방", "#b78120"))
    composition = statistics.get("composition_percent", {})
    values = [ratio(composition.get(key)) for key, _, _ in labels]
    if all(value is not None for value in values) and sum(values) > 0:
        if abs(sum(values) - 100) > 0.02:
            raise ValueError("invalid_public_composition")
        position = 72.0
        for index, ((_, label, color), value) in enumerate(zip(labels, values)):
            width = 976 * value / 100
            parts.append(f'<rect x="{position:.2f}" y="455" width="{width:.2f}" height="30" fill="{color}"/>')
            legend_x = 72 + index * 320
            parts.extend([
                f'<circle cx="{legend_x + 6}" cy="521" r="6" fill="{color}"/>',
                f'<text x="{legend_x + 22}" y="527" font-size="19">{label} {value:.2f}%</text>',
            ])
            position += width
    else:
        parts.append('<text x="72" y="487" class="muted">함께 확인된 영양정보가 부족해 구성비를 계산하지 않았습니다.</text>')
    parts.append('<text x="72" y="565" class="small">탄수화물·단백질·지방이 함께 확인된 항목의 환산 열량을 100%로 계산합니다.</text>')
    qualifiers = []
    if statistics.get("composition_partial"):
        qualifiers.append("불완전한 항목 제외")
    if statistics.get("composition_estimated"):
        qualifiers.append("추정 영양정보 포함")
    if qualifiers:
        parts.append(f'<text x="72" y="590" class="small">{" · ".join(qualifiers)}</text>')
    parts.extend([
        '<text x="48" y="650" font-size="18" font-weight="600">기록 → 음식 사전 연결 → 구성비 확인 → 기록 보완</text>',
        '<text x="48" y="683" class="small">미기록은 결식이 아닙니다. 연결률은 1개 이상 연결된 기록 기준이며 모든 영양소의 완전성을 뜻하지 않습니다.</text>',
        '<text x="48" y="708" class="small">실제 날짜·음식명·섭취량·체중·소비 열량은 제외했습니다. 비율도 개인 기록에서 파생된 결과입니다.</text>',
        '<text x="48" y="733" class="small">날짜 미확인 기록은 제외합니다. 파일을 확인한 뒤 공유 여부를 선택하세요.</text>',
        '</svg>',
    ])
    return "".join(parts)




def render_public_daily_svg(statistics):
    """Separate opt-in timeline image, rebuilt from allowlisted public percentages."""
    if statistics.get("mode") == "private":
        statistics = public_view(statistics)
    if statistics.get("mode") != "public":
        raise ValueError("public_statistics_required")
    keys = ("intake", "intake_change_percent", "carbohydrate_percent", "protein_percent", "fat_percent")
    days = []
    for index, row in enumerate(statistics["days"], 1):
        projected = {"label": f"{index}일차"}
        for key in keys:
            value = row.get(key)
            parsed = None if value is None else (_signed_number(value) if key == "intake_change_percent" else number(value))
            if parsed is not None and key != "intake_change_percent" and parsed > 100:
                raise ValueError("invalid_public_percentage")
            projected[key] = None if parsed is None else str(parsed)
        projected["composition_partial"] = bool(row.get("composition_partial"))
        projected["composition_estimated"] = bool(row.get("composition_estimated"))
        days.append(projected)
    panels = (
        ("daily-energy", "기간 섭취 열량의 날짜별 비중", [("intake", "기간 내 기록 열량 중 비중", "#176b5b")], False),
        ("daily-change", "전일 대비 기록 열량 증감률", [("intake_change_percent", "전일 대비 증감률", "#536a9d")], True),
        ("daily-composition", "날짜별 영양 구성비", [
            ("carbohydrate_percent", "탄수화물", "#176b5b"),
            ("protein_percent", "단백질", "#536a9d"),
            ("fat_percent", "지방", "#b78120")], False),
    )
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1120 1370" width="1120" height="1370" role="img" aria-labelledby="daily-title daily-description">',
        '<title id="daily-title">식사 기록 · 공개용 날짜별 비율과 변화</title>',
        '<desc id="daily-description">상대 날짜별 기록 열량 비중, 전일 증감률, 영양 구성비입니다. 실제 날짜와 절대량은 포함하지 않습니다.</desc>',
        '<style>text{font-family:"Noto Sans KR","Malgun Gothic",sans-serif;fill:#24342f}.grid{stroke:#e6ece8}.tick{font-size:12px;fill:#60746a}.muted{fill:#60746a;font-size:15px}</style>',
        '<rect width="1120" height="1370" rx="24" fill="#f4f6f5"/>',
        '<text x="48" y="53" font-size="14" letter-spacing="2">MEAL JOURNAL / DAILY RATIOS</text>',
        '<text x="48" y="98" font-size="32" font-weight="700">날짜별로 보는 비중과 변화</text>',
        '<text x="48" y="132" class="muted">날짜는 1일차부터 표시하고, 기록되지 않은 구간은 연결하지 않습니다.</text>',
    ]
    for index, (chart_id, title, series, signed) in enumerate(panels):
        top = 158 + index * 355
        parts.extend([
            f'<rect x="48" y="{top}" width="1024" height="337" rx="16" fill="white" stroke="#dce5df"/>',
            f'<text x="72" y="{top + 36}" font-size="22" font-weight="600">{title}</text>',
        ])
        for legend_index, (_, label, color) in enumerate(series):
            legend_x = 72 + legend_index * 300
            parts.append(f'<circle cx="{legend_x + 5}" cy="{top + 57}" r="5" fill="{color}"/>')
            parts.append(f'<text x="{legend_x + 18}" y="{top + 62}" class="muted">{label} (%)</text>')
        plot_days = [dict(day, label=day["label"] + ("*" if day["composition_partial"] or day["composition_estimated"] else "")) for day in days] if chart_id == "daily-composition" else days
        chart = _chart(chart_id, title, plot_days, series, "%", True, signed=signed)
        if "<svg" in chart:
            inner = chart[chart.index("<svg"):chart.index("</svg>") + len("</svg>")]
            inner = inner.replace('<svg ', f'<svg x="64" y="{top + 68}" width="990" height="265" ', 1)
            parts.append(inner)
        else:
            parts.append(f'<text x="72" y="{top + 160}" class="muted">계산 가능한 기록이 없습니다.</text>')
    parts.extend([
        '<text x="48" y="1257" class="muted">증감률 = (당일 기록 열량 − 전날 기록 열량) / 전날 기록 열량 × 100</text>',
        '<text x="48" y="1283" class="muted">첫날·전날 미기록·전날 0인 증감률은 미확인입니다. 비교값은 연결된 기록의 부분합입니다.</text>',
        '<text x="48" y="1309" class="muted">영양 구성비는 각 날짜의 삼대영양소 환산 열량 기준입니다. *는 불완전 항목 제외 또는 추정값 포함입니다.</text>',
        '<text x="48" y="1335" class="muted">실제 날짜·음식명·섭취량·체중·절대 kcal는 제외했습니다. 날짜별 비율도 개인 패턴을 드러낼 수 있습니다.</text>',
        '</svg>',
    ])
    return "".join(parts)


def render_html(statistics):
    if statistics.get("mode") not in {"private", "public"}:
        raise ValueError("invalid_statistics_mode")
    public = statistics["mode"] == "public"
    days = statistics["days"]
    heading = "공개용 구성비" if public else "개인용"
    period = f'{days[0]["label"]} — {days[-1]["label"]}' if days else ""
    energy_unit = "%" if public else "kcal"
    weight_unit = "kg"
    export = ""
    if not public:
        shared = render_html(public_view(statistics)).encode("utf-8")
        encoded = base64.b64encode(shared).decode("ascii")
        export = f'<a class="download" download="meal-statistics-public.html" href="data:text/html;charset=utf-8;base64,{encoded}">공개용 구성비 파일 저장</a>'
    blog_svg = render_public_svg(statistics)
    blog_encoded = base64.b64encode(blog_svg.encode("utf-8")).decode("ascii")
    export += f'<a class="download" download="meal-management-public.svg" href="data:image/svg+xml;base64,{blog_encoded}">블로그용 요약 이미지 저장</a>'
    daily_svg = render_public_daily_svg(statistics)
    daily_encoded = base64.b64encode(daily_svg.encode("utf-8")).decode("ascii")
    export += f'<a class="download" download="meal-daily-public.svg" href="data:image/svg+xml;base64,{daily_encoded}">블로그용 날짜별 이미지 저장</a>'
    estimate = statistics.get("energy_estimate", {})
    estimate_text = ""
    if not public:
        if estimate.get("daily_kcal") is not None:
            estimate_text = f'휴식대사량 추정 {float(number(estimate["resting_kcal_per_day"])):.0f} kcal · 하루 소비 열량 추정 {float(number(estimate["daily_kcal"])):.0f} kcal'
        else:
            estimate_text = "소비 열량 추정에는 로컬 신체 정보와 활동계수가 필요합니다."
        if estimate.get("outside_original_age_range"):
            estimate_text += " 입력 연령은 원 논문의 연구 대상 범위(19–78세)를 벗어납니다."
    series = [("intake", "기록된 섭취", "#176b5b"),
              ("estimated_intake", "섭취 중 추정 부분", "#b78120")]
    if not public:
        series.append(("expenditure", "소비 열량 추정", "#536a9d"))
    title = "기간 섭취 열량의 날짜별 비중" if public else "기록된 섭취 열량과 소비 열량 추정"
    energy = _chart("energy", title, days, series, energy_unit, True)
    change = _chart("change", "전일 대비 기록 열량 증감률", days, [("intake_change_percent", "전일 대비 증감률", "#536a9d")], "%", True, signed=True)
    daily_composition = _chart("daily-macros", "날짜별 영양 구성비", days, [
        ("carbohydrate_percent", "탄수화물", "#176b5b"),
        ("protein_percent", "단백질", "#536a9d"),
        ("fat_percent", "지방", "#b78120")], "%", True)
    weight = "" if public else _chart("weight", "관측된 체중", days, [("weight", "체중 일평균", "#7453a1")], weight_unit, False)
    composition = f'<section class="chart">{blog_svg}</section>' if public else _composition(statistics)
    keys = ("intake", "estimated_intake") if public else ("intake", "estimated_intake", "expenditure", "weight")
    headers = [f"기록된 섭취 ({energy_unit})", f"추정 섭취 부분 ({energy_unit})"]
    if not public:
        headers.extend(["소비 추정 (kcal)", "체중 (kg)"])
    if not public:
        keys += ("intake_change_kcal",)
        headers.append("전일 증감량 (kcal)")
    keys += ("intake_change_percent", "carbohydrate_percent", "protein_percent", "fat_percent")
    headers.extend(["전일 증감률 (%)", "탄수화물 (%)", "단백질 (%)", "지방 (%)"])
    table_header = "<th>날짜</th>" + "".join(f"<th>{escape(label)}</th>" for label in headers) + "<th>영양 구성 상태</th>"
    rows = []
    for day in days:
        cells = [escape(day["label"])]
        cells.extend("—" if day.get(key) is None else f"{float(_signed_number(day[key])):.2f}" for key in keys)
        quality = []
        if day.get("carbohydrate_percent") is None:
            quality.append("계산 불가")
        if day.get("composition_partial"):
            quality.append("일부 제외")
        if day.get("composition_estimated"):
            quality.append("추정 포함")
        cells.append(" · ".join(quality) or "계산 가능한 항목 기준")
        rows.append("<tr>" + "".join(f"<td>{cell}</td>" for cell in cells) + "</tr>")
    notes = "".join(f"<li>{escape(note)}</li>" for note in statistics["notes"])
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="referrer" content="no-referrer"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; form-action 'none'">
<title>식사·체중 통계 · {heading}</title>
<style>
*{{box-sizing:border-box}}body{{margin:0;background:#f4f6f5;color:#24342f;font-family:Inter,"Noto Sans KR",sans-serif;line-height:1.6}}
main{{max-width:1040px;margin:0 auto;padding:32px 20px}}header{{display:flex;align-items:center;justify-content:space-between;gap:20px;flex-wrap:wrap}}
h1{{font-size:27px;margin:0}}h2{{font-size:18px;margin:0 0 14px}}p{{margin:8px 0}}.meta,.empty{{color:#586961}}
.download{{display:inline-block;background:#176b5b;color:white;padding:10px 16px;border-radius:8px;text-decoration:none}}
.chart,.details{{background:white;border:1px solid #dce5df;border-radius:12px;padding:22px;margin:20px 0}}
svg{{display:block;width:100%;height:auto}}.grid{{stroke:#e6ece8}}.tick{{font-size:12px;fill:#60746a}}
.composition{{display:flex;height:24px;border-radius:6px;overflow:hidden;margin:18px 0}}.composition span{{display:block}}
.legend{{display:flex;flex-wrap:wrap;gap:18px;font-size:13px}}.legend i{{display:inline-block;width:10px;height:10px;border-radius:50%;margin-right:6px}}
table{{border-collapse:collapse;width:100%;font-size:13px}}td,th{{text-align:right;border-bottom:1px solid #e7ece9;padding:9px}}td:first-child,th:first-child{{text-align:left}}
.table-scroll{{overflow-x:auto}}summary{{cursor:pointer;font-weight:600}}ul{{padding-left:20px}}@media(max-width:600px){{main{{padding:20px 12px}}.chart,.details{{padding:14px}}h1{{font-size:23px}}}}
</style></head><body><main>
<header><div><h1>식사·체중 통계</h1><p class="meta">{heading} · {escape(period)}</p></div>{export}</header>
<p>{escape(estimate_text)}</p>{composition}{energy}{change}{daily_composition}{weight}
<details class="details"><summary>날짜별 값 보기</summary><div class="table-scroll"><table>
<thead><tr>{table_header}</tr></thead>
<tbody>{"".join(rows)}</tbody></table></div></details><ul>{notes}</ul>
</main></body></html>'''
