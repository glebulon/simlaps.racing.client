"""Static response-contract renderer."""

from typing import List

from src.core.car_tuning_catalog import format_tuning_block

from .context import PromptContext


def render_response_contract(ctx: PromptContext) -> List[str]:
    car_model = ctx.car_model
    car_known = car_model != "Unknown Car"
    track_label = ctx.track_label
    tuning_block = format_tuning_block(car_model) if car_known else ""
    lines: List[str] = []
    lines.append("RESPONSE FORMAT — FOLLOW EXACTLY. NO DEVIATION.")
    lines.append("")
    lines.append("CRITICAL STYLE RULE: Be extremely concise. Each bullet is ONE short actionable sentence.")
    lines.append("The driver reads this at a glance between sessions. Information overload = useless.")
    lines.append("Good example: 'Brake 1s sooner and turn in later to setup for exit (+0.7s).'")
    lines.append(
        "Bad example: 'Lap 4 applies 1.02G peak braking with 25% trail brake into a corner where Lap 2 peaks at 0.35G — remove the brake input entirely.'"  # noqa: E501
    )
    lines.append("One supporting number per bullet maximum. No multi-stat comparisons. No lap-vs-lap narration.")
    lines.append("")
    lines.append("Output in clean Markdown. Use ## for section headers.")
    lines.append("Use bullet points inside sections. Never use numbered lists inside bullets.")
    lines.append("No padding. No preamble.")
    lines.append("Start your response directly with '## [Car] — [Track Name] Debrief'")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 1. TOP 3 TIME-LOSS CORNERS")
    lines.append("")
    lines.append("Exclude any corners flagged as corrupt/capped in the data above.")
    lines.append("Format each corner EXACTLY like this:")
    lines.append("")
    lines.append("- **[Corner Name]** | -[delta]s | [short action to fix it]")
    lines.append("")
    lines.append(
        "The action must be a direct instruction (e.g. 'lift instead of braking', 'brake 0.5s earlier', 'carry 15 km/h more apex speed')."  # noqa: E501
    )
    lines.append("Do NOT explain the cause in detail — just state what to change.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 2. DRIVING TECHNIQUE")
    lines.append("")
    lines.append("5 bullets maximum. Each bullet is ONE short instruction the driver can act on immediately.")
    lines.append("Format: **[Corner]:** [do X] ([one supporting number]).")
    lines.append("")
    lines.append("Examples of correct brevity:")
    lines.append("  - **Turn 3:** Brake 0.5s earlier and trail deeper to hold 94 km/h apex.")
    lines.append("  - **Rainey Curve:** Commit to turn-in 0.3s sooner — no braking after initial lift.")
    lines.append("  - **Turn 10:** Get on throttle at apex — coasting loses 17 km/h exit speed.")
    lines.append("")
    lines.append("Do NOT say 'consider' or 'try'. Do NOT cite multiple numbers or compare laps inline.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 3. CONSISTENCY")
    lines.append("")
    lines.append("3 bullets maximum. One line each.")
    lines.append(
        "Format: **[Corner]:** [apex range] km/h spread — [one-phrase cause: 'no braking marker' or 'commitment varies']."  # noqa: E501
    )
    lines.append("")
    lines.append("---")
    lines.append("")
    if car_known:
        lines.append(f"## 4. CAR SETUP — {car_model}")
        lines.append("")
        lines.append("Output a Markdown table with exactly these columns:")
        lines.append("| Parameter | Signal | Change |")
        lines.append("| --- | --- | --- |")
        lines.append("")
        lines.append("Rules:")
        lines.append("- Maximum 4 rows. Only where telemetry gives a CLEAR signal.")
        lines.append("- 'Signal' = one data point (e.g. '28.4 psi hot', 'peak brake temp 620C').")
        lines.append("- 'Change' = short directional action (e.g. 'reduce 0.5 psi', 'raise rear 1 step').")
        lines.append("- Parameter and Signal MUST describe the same subsystem. Never mix evidence across systems.")
        lines.append(
            "- Tyre pressure rows MUST use tyre pressure evidence in psi only -- never brake temperature, tyre temperature, or wear."  # noqa: E501
        )
        lines.append(
            "- Brake temperature evidence may only support brake-related parameters, and only if that brake-related parameter is listed as adjustable for this car."  # noqa: E501
        )
        lines.append("- If you do not have a matching telemetry signal for a parameter, omit that row entirely.")
        if tuning_block:
            lines.append("- ONLY recommend parameters listed in the CAR SETUP PARAMETERS section above.")
            lines.append("- If a parameter is NOT in that list, the car cannot adjust it -- do NOT suggest it.")
        else:
            lines.append("- Do NOT assume brake bias is adjustable -- many cars have fixed brake bias.")
            lines.append("- Only recommend parameters you are certain this car can adjust in AC Evo.")
    else:
        lines.append("## 4. CAR SETUP — SKIPPED")
        lines.append("")
        lines.append("Car identity unknown. Skip.")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 5. STRAIGHTS & SECTORS")
    lines.append("")
    lines.append("3 bullets maximum. One line each.")
    lines.append("Use the STRAIGHT/SECTOR ANALYSIS and EXIT-TO-ENTRY CORRELATION data above.")
    lines.append("Format: **[Corner A → Corner B]:** [insight] ([one number]).")
    lines.append(
        "Only include straights where there is meaningful time spread between laps or exit speed is compromising the next corner."  # noqa: E501
    )
    lines.append("Example: '**T2 → T3:** Poor T2 exit costs 0.4s on straight — get on throttle 0.3s earlier.'")
    lines.append("Example: '**T4 → T5:** 0.5s spread on straight — carry more speed through T4 exit.'")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(f"## 6. TRACK NOTES — {track_label}")
    lines.append("")
    lines.append("3 bullets maximum. One line each.")
    lines.append("Format: **[Corner/Section]:** [short insight] ([one number]).")
    lines.append(
        "Only include observations where the car has significant unused grip or the corner can be taken differently than expected."  # noqa: E501
    )
    lines.append("Example: '**Blanchimont:** Can be taken flat — only 0.77G used vs 2.26G available.'")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## 7. SINGLE BIGGEST GAIN")
    lines.append("")
    lines.append("Exactly one sentence: [corner] + [what to change] + [expected delta].")
    lines.append("Example: 'Lift instead of braking into Turn 6 to gain ~0.7s.'")
    lines.append("No hedging. No 'this could' or 'potentially'.")
    lines.append("")
    lines.append("=" * 60)
    lines.append("REMEMBER: Brevity is paramount. The driver needs quick, actionable cues — not a data thesis.")
    lines.append("Each bullet = one action + one number. If a section has no actionable data, skip it entirely.")
    lines.append("=" * 60)
    return lines
