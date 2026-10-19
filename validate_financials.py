# -*- coding: utf-8 -*-
"""Validate financials JSON: parse, check array lengths and keys."""
import json
from pathlib import Path

p = Path(r"C:\Users\Chang\OneDrive\Desktop\agent 开发\agents\research-report-agent\data\600256_financials.json")
data = json.loads(p.read_text(encoding="utf-8"))
print("parse OK, top keys:", list(data.keys()))

YEAR_SETS = {
    "segments.coal": data["segments"]["coal"],
    "segments.natural_gas": data["segments"]["natural_gas"],
    "segments.coal_chemical": data["segments"]["coal_chemical"],
    "segments.total": data["segments"]["total"],
    "income_statement": data["income_statement"],
    "balance_sheet": data["balance_sheet"],
    "cash_flow": data["cash_flow"],
    "ratios": data["ratios"],
    "earnings_forecast": data["earnings_forecast"],
    "valuation_comparables": data["valuation_comparables"],
}
errors = []
for name, obj in YEAR_SETS.items():
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, list) and k not in ("years", "unit", "note", "driver", "rating", "upgrade_note", "as_of", "year", "previous_forecast_yi", "net_profit_attributable_yi", "eps", "pe_at_6.67"):
                if len(v) != 5:
                    errors.append(f"{name}.{k}: len={len(v)}")
print("array-length check:", "ALL 5 OK" if not errors else errors)

# spot-check key numbers against the PDF
seg = data["segments"]
checks = [
    ("coal rev 2028E", seg["coal"]["revenue"][4], 369.26),
    ("gas vol 2026E", seg["natural_gas"]["sales_volume"][2], 24.11),
    ("total rev 2028E", seg["total"]["revenue"][4], 501.96),
    ("NP 2026E", data["earnings_forecast"]["net_profit_attributable_yi"][0], 39.76),
    ("eps 2028E", data["ratios"]["eps_diluted"][4], 0.89),
    ("pe 2026E", data["ratios"]["pe"][2], 10.7),
    ("gross margin 2026E", data["ratios"]["gross_margin_pct"][2], 28.0),
]
bad = [f"{n}: {got} != {want}" for n, got, want in checks if abs(got - want) > 1e-9]
print("spot checks:", "ALL PASS" if not bad else bad)
