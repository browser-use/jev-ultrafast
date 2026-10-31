import sys
sys.stdout.reconfigure(encoding='utf-8')
import os
import json
from jev_ultrafast.demo import load_environment

load_environment()
from jev_ultrafast.browser import Browser
from jev_ultrafast.model import action_space

os.environ['BU_CDP_URL'] = 'http://127.0.0.1:9222'
b = Browser('http://127.0.0.1:8766/fixture.html?scenario=travel')
page = b.observe(screenshot=False)
b.close()

elements, targets, controls = action_space(page['actions'])
print("PAGE TITLE:", page["title"])
print("PAGE TEXT:\n", page["text"][:300])
print("\nAVAILABLE OPERATIONS:")
for op, cand in targets.items():
    print(f"\n--- {op} ({len(cand)} options) ---")
    for idx, c in cand.items():
        print(f"  [{idx}] id={c['id']} label='{c.get('label')}' role='{c.get('role')}' kind='{c.get('kind')}' val='{c.get('value', '')}'")

print("\nCONTROLS:")
for k, v in controls.items():
    print(f"  {k}: {v['label']}")
