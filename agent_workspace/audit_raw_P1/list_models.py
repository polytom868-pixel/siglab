import json, sys
d = json.load(sys.stdin)
for m in d["data"]:
    if ":free" in m["id"]:
        top = m.get("top_provider") or {}
        arch = m.get("architecture") or {}
        print(
            f"{m['id']:50s} | ctx={m.get('context_length', '?')} | "
            f"modality={arch.get('input_modalities')} | "
            f"pricing_prompt={m.get('pricing',{}).get('prompt')} | "
            f"pricing_completion={m.get('pricing',{}).get('completion')} | "
            f"top_provider_name={top.get('name') if top else None}"
        )
