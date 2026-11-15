"""Deterministic observed-record summaries; no prescriptions or causal attribution."""
from collections import Counter, defaultdict
from datetime import timedelta, timezone
from decimal import Decimal, localcontext
import hashlib
from .catalog import canonical, iso_date, number
from .journal import timestamp

KST = timezone(timedelta(hours=9))
MACRO_FACTORS = {"carbohydrate_g": 4, "protein_g": 4, "fat_g": 9}


def event_day(event):
    if event.get("occurred_on"):
        return event["occurred_on"]
    if event.get("occurred_at"):
        return timestamp(event["occurred_at"]).astimezone(KST).date().isoformat()
    return None


def build(journal, catalog, start, end, *, include_statistics=False):
    first, last = iso_date(start), iso_date(end)
    if first > last:
        raise ValueError("invalid_report_range")
    all_rows = journal.current()
    meals, weights, untimed = [], [], []
    for event in all_rows:
        day = event_day(event)
        if day and start <= day <= end:
            (meals if event["kind"] == "meal" else weights).append(event)
        elif not day and start <= timestamp(event["received_at"]).astimezone(KST).date().isoformat() <= end:
            untimed.append(event)
    confirmed = defaultdict(Decimal)
    estimated = defaultdict(Decimal)
    counts = Counter()
    refs = set()
    foods = Counter()
    food_names = {}
    details = []
    macro_energy = defaultdict(Decimal)
    macro_counts = Counter()
    daily_macro_energy = defaultdict(lambda: defaultdict(Decimal))
    daily_macro_counts = defaultdict(Counter)
    linked_meal_records = 0
    daily_nutrients = defaultdict(lambda: {"confirmed": defaultdict(Decimal), "estimated": defaultdict(Decimal)})
    for meal in meals:
        has_nutrition_link = False
        meal_day = event_day(meal)
        if not meal.get("components"):
            counts["records_without_nutrition"] += 1
            if include_statistics:
                daily_macro_counts[meal_day]["excluded_inputs"] += 1
        for component in meal.get("components", []):
            try:
                profile = catalog.get(component["nutrition_ref"])
            except ValueError:
                counts["missing_or_invalid_reference"] += 1
                if include_statistics:
                    daily_macro_counts[meal_day]["excluded_inputs"] += 1
                continue
            if profile["review_state"] != "verified":
                counts["unreviewed_component"] += 1
                if include_statistics:
                    daily_macro_counts[meal_day]["excluded_inputs"] += 1
                continue
            if profile["basis"]["unit"] != component["unit"]:
                counts["unit_mismatch"] += 1
                if include_statistics:
                    daily_macro_counts[meal_day]["excluded_inputs"] += 1
                continue
            factor = number(component["amount"], True) / number(profile["basis"]["quantity"], True)
            uncertainty = profile.get("uncertainty")
            certainty = "estimated" if uncertainty else component["certainty"]
            bucket = confirmed if certainty == "confirmed" else estimated
            for nutrient, value in profile["nutrients"].items():
                contribution = number(value) * factor
                bucket[nutrient] += contribution
                daily_nutrients[event_day(meal)][certainty][nutrient] += contribution
            has_nutrition_link = has_nutrition_link or bool(profile["nutrients"])
            counts[certainty + "_components"] += 1
            refs.add(component["nutrition_ref"])
            foods[profile["food_id"]] += 1
            food_names[profile["food_id"]] = profile["name"]
            if include_statistics:
                if all(key in profile["nutrients"] for key in MACRO_FACTORS):
                    for key, energy_factor in MACRO_FACTORS.items():
                        contribution = number(profile["nutrients"][key]) * factor * energy_factor
                        macro_energy[key] += contribution
                        daily_macro_energy[meal_day][key] += contribution
                    macro_counts["included_components"] += 1
                    macro_counts["estimated_components"] += certainty == "estimated"
                    daily_macro_counts[meal_day]["included_components"] += 1
                    daily_macro_counts[meal_day]["estimated_components"] += certainty == "estimated"
                else:
                    macro_counts["incomplete_components"] += 1
                    daily_macro_counts[meal_day]["incomplete_components"] += 1
            detail = {"event_id": meal["event_id"], "nutrition_ref": component["nutrition_ref"], "certainty": certainty, "nutrients_available": sorted(profile["nutrients"])}
            if uncertainty:
                counts["estimated_profiles"] += 1
                detail["input_certainty"] = component["certainty"]
                detail["estimation_reason"] = uncertainty["reason"]
                detail["nutrient_ranges"] = {
                    key: {bound: str(number(value) * factor) for bound, value in bounds.items()}
                    for key, bounds in uncertainty["ranges"].items()
                }
            details.append(detail)
        linked_meal_records += has_nutrition_link
    by_day = defaultdict(list)
    for measurement in weights:
        by_day[event_day(measurement)].append(number(measurement["kg"], True))
    means = {day: sum(values) / len(values) for day, values in by_day.items()}
    ordered_days = sorted(means)
    daily_means = {day: str(means[day]) for day in ordered_days}
    weight_info = {"measurement_count": len(weights), "measured_days": len(means), "daily_mean_kg": daily_means, "period_daily_mean_kg": str(sum(means.values()) / len(means)) if means else None, "change_between_observed_days_kg": str(means[ordered_days[-1]] - means[ordered_days[0]]) if len(means) >= 2 else None}
    slots = Counter()
    for meal in meals:
        if meal.get("occurred_at"):
            slots[str(timestamp(meal["occurred_at"]).astimezone(KST).hour)] += 1
    observed_days = sorted({event_day(m) for m in meals + weights})
    daily = [{
        "date": day,
        "meal_record_count": sum(event_day(m) == day for m in meals),
        "nutrition_confirmed_partial": {k: str(v) for k, v in sorted(daily_nutrients[day]["confirmed"].items())},
        "nutrition_estimated_partial": {k: str(v) for k, v in sorted(daily_nutrients[day]["estimated"].items())},
        "weight_daily_mean_kg": daily_means.get(day),
    } for day in observed_days]
    report = {
        "schema_version": 1, "start": start, "end": end, "timezone": "Asia/Seoul",
        "meal_record_count": len(meals), "recorded_days": len({event_day(m) for m in meals}),
        "untimed_record_count": len(untimed), "explicit_meal_hour_counts": dict(slots),
        "nutrition_confirmed_partial": {k: str(v) for k, v in sorted(confirmed.items())},
        "nutrition_estimated_partial": {k: str(v) for k, v in sorted(estimated.items())},
        "nutrition_coverage": dict(counts), "component_details": details,
        "nutrition_refs": sorted(refs), "weight": weight_info, "daily_observations": daily,
        "linked_food_occurrences": [{"food_id": key, "name": food_names[key], "component_count": count} for key, count in sorted(foods.items(), key=lambda pair: (-pair[1], pair[0]))],
        "record_revisions": sorted([[r["event_id"], r["revision"]] for r in meals + weights + untimed]),
        "limitations": [
            "Sums describe linked recorded intake only, not a complete daily intake.",
            "Unrecorded meals and missing nutrients are unknown, not zero.",
            "Untimed entries are preserved but excluded from dated intake totals.",
            "Observed weight and eating patterns do not establish causation.",
        ],
    }
    if include_statistics:
        for row in daily:
            row["macronutrient_composition"] = {
                "energy_kcal": {key: str(value) for key, value in sorted(daily_macro_energy[row["date"]].items())},
                **dict(daily_macro_counts[row["date"]]),
            }
        report["management_statistics"] = {
            "recorded_days": report["recorded_days"],
            "meal_record_count": len(meals),
            "nutrition_linked_meal_record_count": linked_meal_records,
        }
        report["macronutrient_composition"] = {
            "energy_kcal": {key: str(value) for key, value in sorted(macro_energy.items())},
            **dict(macro_counts),
        }
    report["report_hash"] = hashlib.sha256(canonical(report).encode()).hexdigest()
    return report


def markdown(report):
    c = report["nutrition_coverage"]
    lines = [f"# 식사·체중 기록 요약 {report['start']} ~ {report['end']}",
             f"기록된 식사 {report['meal_record_count']}건 · 기록일 {report['recorded_days']}일 · 날짜 미확인 {report['untimed_record_count']}건",
             "", "## 영양정보 연결",
             f"확정 연결 {c.get('confirmed_components', 0)}개 · 추정 연결 {c.get('estimated_components', 0)}개 · 영양 연결 없는 기록 {c.get('records_without_nutrition', 0)}건"]
    labels = {"energy_kcal": ("열량", "kcal"), "protein_g": ("단백질", "g"), "carbohydrate_g": ("탄수화물", "g"), "fat_g": ("지방", "g"), "fiber_g": ("식이섬유", "g"), "sodium_mg": ("나트륨", "mg"), "sugars_g": ("당류", "g"), "saturated_fat_g": ("포화지방", "g")}
    def show(value):
        parsed = Decimal(value)
        with localcontext() as context:
            context.prec = max(28, len(parsed.as_tuple().digits) + abs(parsed.adjusted()) + 8)
            return str(parsed.quantize(Decimal("0.01")))
    for label, key in [("확정 부분합", "nutrition_confirmed_partial"), ("추정 부분합", "nutrition_estimated_partial")]:
        values = report[key]
        lines.append(label + ": " + (", ".join(f"{labels[k][0]} {show(v)} {labels[k][1]}" for k, v in values.items()) if values else "계산 가능한 값 없음"))
    if c.get("estimated_profiles"):
        lines.append("사전 자체의 추정값은 양이 확인돼도 추정 부분합에 포함합니다. 범위는 작업용 추정이며 통계적 신뢰구간이 아닙니다.")
    for label in ("missing_or_invalid_reference", "unreviewed_component", "unit_mismatch"):
        if c.get(label):
            reason = {"missing_or_invalid_reference": "참조 누락·무결성 확인", "unreviewed_component": "영양정보 원문 검토", "unit_mismatch": "섭취량 단위 확인"}[label]
            lines.append(f"보완 필요: {reason} {c[label]}개")
    lines += ["", "## 확인된 기록 패턴"]
    foods = report["linked_food_occurrences"][:5]
    lines.append("영양정보에 연결된 식품 등장: " + (", ".join(f"{f['name']} {f['component_count']}회" for f in foods) if foods else "확인된 연결 없음"))
    hours = sorted(report["explicit_meal_hour_counts"].items(), key=lambda pair: int(pair[0]))
    lines.append("명시된 식사 시간대: " + (", ".join(f"{h}시 {count}건" for h, count in hours) if hours else "시간 정보 없음"))
    weight = report["weight"]
    lines += ["", "## 관측된 체중", f"측정 {weight['measurement_count']}회 · 측정일 {weight['measured_days']}일",
              "측정일 평균: " + (show(weight["period_daily_mean_kg"]) + " kg" if weight["period_daily_mean_kg"] is not None else "미확인"),
              "첫·마지막 관측일 차이: " + (show(weight["change_between_observed_days_kg"]) + " kg" if weight["change_between_observed_days_kg"] is not None else "비교할 날짜 부족"),
              "", "## 해석 범위", "기록·연결된 섭취의 부분 집계입니다. 미기록은 결식이나 0이 아닙니다.",
              "체중과 식습관의 원인을 확정하거나 영양 처방·감량 목표를 만들지 않습니다."]
    if len(report["daily_observations"]) > 1:
        lines += ["", "## 날짜별 관측", "날짜 | 식사 기록 | 열량 확정 부분합 | 열량 추정 부분합 | 체중 평균", "--- | ---: | ---: | ---: | ---:"]
        for day in report["daily_observations"]:
            confirmed = day["nutrition_confirmed_partial"].get("energy_kcal")
            estimated = day["nutrition_estimated_partial"].get("energy_kcal")
            weight = day["weight_daily_mean_kg"]
            lines.append(f"{day['date']} | {day['meal_record_count']}건 | {show(confirmed) + ' kcal' if confirmed is not None else '미확인'} | {show(estimated) + ' kcal' if estimated is not None else '미확인'} | {show(weight) + ' kg' if weight is not None else '미측정'}")
    return "\n".join(lines) + "\n"
