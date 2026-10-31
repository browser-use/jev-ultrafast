# TypeSafe AI / Jev Complete System Specification & Operating Standard

> **MANDATORY OPERATIONAL DIRECTIVE:**
> This document is the absolute single source of truth for all decision-making, evaluation, classification, and scoring tasks. 
> **You MUST USE JEV AI ONLY.** Do not use generic generative LLMs for routing, evaluation, scoring, classification, or decision-making. All evaluation questions must be formulated as Jev System 1 primitives (`choice`, `score`, `null`) and executed through TypeSafe Jev.

---

## 1. System Philosophy: The Jev "System 1" Paradigm

Traditional Large Language Models (LLMs) operate as **System 2** generative engines: they are slow, verbose, non-deterministic in format, prone to hallucinations, and require complex prompt engineering and fragile output parsing.

**TypeSafe AI's Jev** is a **System 1 Decision Engine**:
- **Pure Reflexive Decision-Making:** Takes unstructured context (text, DOM state, customer tickets, job descriptions, logs) and returns strictly typed, structured decisions.
- **Single Round-Trip Atomic Evaluation:** Evaluates multiple orthogonal questions (categories, scores, boolean flags) in parallel against a single state in one network call.
- **Calibrated Probabilities:** Returns exact confidence scores and full probability distributions over candidate options.
- **Zero Token Waste:** Outputs structured numeric values and category choices rather than conversational boilerplate.

---

## 2. Core Question Primitives

Jev operates on three fundamental primitive question types. Every evaluation rubric must be built from these primitives:

| Primitive | Type Name | Input Configuration | Return Value & Fields | Common Use Cases |
|---|---|---|---|---|
| **Categorical** | `"choice"` | `instructions`: str<br>`choices`: list[str] | `choice`: str (winner)<br>`confidence`: float (0.0–1.0)<br>`probabilities`: dict[str, float] | Department routing, Job triage (`APPLY NOW`, `STRETCH`, `SKIP`), action picking |
| **Scalar / Metric** | `"score"` | `instructions`: str<br>`scale`: [min, max] (e.g. `[0, 10]`)<br>*optional* `labels`: list[str] | `score`: float<br>`label`: str (if configured) | Match quality, frustration rating, technical depth, relevance score |
| **Boolean / Probability** | `"null"` | `instructions`: str (yes/no question) | `value`: float (probability 0.0–1.0) | Urgency flag, location match (`is_nyc_nj`), clearance required, spam detector |

---

## 3. Direct API Specification (cURL / HTTP)

The raw HTTP interface hits the TypeSafe System 1 endpoint:

### Endpoint
```http
POST https://api.typesafe.ai/v1/system1
```

### Headers
```http
Authorization: Bearer YOUR_TYPESAFE_API_KEY
Content-Type: application/json
```

### Request Payload Schema
```json
{
  "model": "jev-latest",
  "state": "<unstructured string context, document, or state>",
  "questions": {
    "<question_key_1>": {
      "type": "choice",
      "instructions": "Which action or classification applies?",
      "choices": ["option_a", "option_b", "option_c"]
    },
    "<question_key_2>": {
      "type": "score",
      "instructions": "Rate this dimension on the given scale.",
      "scale": [0, 10]
    },
    "<question_key_3>": {
      "type": "null",
      "instructions": "Does the state satisfy this condition?"
    }
  }
}
```

### cURL Example
```bash
curl -X POST "https://api.typesafe.ai/v1/system1" \
  -H "Authorization: Bearer $TYPESAFE_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "jev-latest",
    "state": "Hi I have been trying to connect my Stripe account for 3 days and it keeps failing. I am losing sales. Please help ASAP.",
    "questions": {
      "department": {
        "type": "choice",
        "instructions": "Which department should handle this customer inquiry?",
        "choices": ["billing", "technical", "sales"]
      },
      "frustration": {
        "type": "score",
        "instructions": "How frustrated does the customer appear?",
        "scale": {
          "min": 0,
          "max": 2,
          "labels": ["calm", "frustrated but civil", "very angry/abusive"]
        }
      },
      "is_urgent": {
        "type": "null",
        "instructions": "Does this message express urgency or time sensitivity?"
      }
    }
  }'
```

### Raw JSON Response Structure
```json
{
  "model": "jev-1.13",
  "answers": {
    "department": {
      "type": "choice",
      "choice": "billing",
      "confidence": 0.57,
      "probabilities": {
        "billing": 0.57,
        "technical": 0.38,
        "sales": 0.05
      }
    },
    "frustration": {
      "type": "score",
      "score": 1.0,
      "label": "frustrated but civil"
    },
    "is_urgent": {
      "type": "null",
      "value": 0.99
    }
  },
  "usage": {
    "input_tokens": 301,
    "output_tokens": 21
  }
}
```

---

## 4. Python SDK Implementation (`typesafe-sdk`)

### Installation & Environment
```bash
pip install typesafe-sdk python-dotenv httpx
```

```env
TYPESAFE_API_KEY=your_typesafe_api_key_here
```

### Standard Implementation Pattern
```python
import os
from dotenv import load_dotenv
from typesafe import TypesafeClient

# 1. Load credentials
load_dotenv()
api_key = os.getenv("TYPESAFE_API_KEY")
client = TypesafeClient(api_key=api_key)

# 2. Prepare unstructured state
state_input = (
    "Hi I've been trying to connect my Stripe account for 3 days and it keeps failing. "
    "I'm losing sales. Please help ASAP."
)

# 3. Define typed evaluation schema
questions = {
    "department": {
        "type": "choice",
        "instructions": "Which department should handle this ticket?",
        "choices": ["billing", "technical", "sales"]
    },
    "frustration": {
        "type": "score",
        "instructions": "How frustrated is this customer?",
        "scale": [0, 2]
    },
    "is_urgent": {
        "type": "null",
        "instructions": "Does this message express urgency?"
    }
}

# 4. Atomic parallel evaluation
response = client.evaluate(
    model="jev-latest",
    state=state_input,
    questions=questions
)

# 5. Extract strictly typed answers
department = response.answers.get("department").choice
confidence = response.answers.get("department").confidence
frustration_score = response.answers.get("frustration").score
urgency_prob = response.answers.get("is_urgent").value

print(f"Department: {department} ({confidence:.1%})")
print(f"Frustration: {frustration_score}")
print(f"Urgency: {urgency_prob:.2f}")
```

---

## 5. Agentic Evaluation Engine Architecture (`rubric.json` + `cli.py`)

In Kyle's walkthrough, an evaluation harness is structured cleanly by separating the rubric configuration from execution:

```text
jev-eval/
├── .env
├── rubric.json
├── cli.py
└── data/
```

### Rubric Definition (`rubric.json`)
```json
{
  "audience": "intermediate to advanced software engineers",
  "dimensions": {
    "hook_strength": {
      "type": "score",
      "instructions": "Evaluate how compelling and immediate the article opening hook is.",
      "scale": [0, 10],
      "weight": 0.15
    },
    "technical_depth": {
      "type": "score",
      "instructions": "Rate the technical rigor, code grounding, and depth of analysis.",
      "scale": [0, 10],
      "weight": 0.25
    },
    "actionability": {
      "type": "score",
      "instructions": "Can the reader immediately execute or build using these steps?",
      "scale": [0, 10],
      "weight": 0.20
    },
    "evidence": {
      "type": "score",
      "instructions": "Are claims backed by clear benchmarks, code examples, or docs?",
      "scale": [0, 10],
      "weight": 0.15
    },
    "voice": {
      "type": "score",
      "instructions": "Consistency of authentic engineer tone without generic AI filler.",
      "scale": [0, 10],
      "weight": 0.15
    },
    "structure": {
      "type": "score",
      "instructions": "Scannability, logical progression, and clean section breaks.",
      "scale": [0, 10],
      "weight": 0.10
    }
  },
  "flags": {
    "title_delivers": {
      "type": "null",
      "instructions": "Does the content directly fulfill what the title promises?"
    },
    "stale_risk": {
      "type": "null",
      "instructions": "Does this rely on fast-deprecating APIs or transient context?"
    },
    "assumes_unstated_setup": {
      "type": "null",
      "instructions": "Does the guide skip critical prerequisites or installation steps?"
    }
  }
}
```

### Evaluation Engine & Composite Scoring (`cli.py`)
```python
import os
import csv
import json
import argparse
from dotenv import load_dotenv
from typesafe import TypesafeClient

load_dotenv()
client = TypesafeClient(api_key=os.getenv("TYPESAFE_API_KEY"))

def load_rubric(path="rubric.json"):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def evaluate_article(article_text: str, rubric: dict) -> dict:
    """Builds question payload from rubric and queries Jev in one atomic call."""
    questions = {}
    
    # 1. Map score dimensions
    for dim_name, config in rubric["dimensions"].items():
        questions[dim_name] = {
            "type": config["type"],
            "instructions": config["instructions"],
            "scale": config["scale"]
        }
        
    # 2. Map boolean flags
    for flag_name, config in rubric["flags"].items():
        questions[flag_name] = {
            "type": config["type"],
            "instructions": config["instructions"]
        }

    # Evaluate all dimensions in a single atomic pass
    response = client.evaluate(
        model="jev-latest",
        state=article_text,
        questions=questions
    )
    return response.answers

def export_to_csv(results: list, output_filepath="eval_results.csv"):
    """Writes a wide-format CSV row per article for spreadsheets."""
    if not results:
        return
    fieldnames = list(results[0].keys())
    with open(output_filepath, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow(row)
    print(f"Results exported to {output_filepath}")

def main():
    parser = argparse.ArgumentParser(description="Evaluate articles with Typesafe Jev")
    parser.add_argument("--file", required=True, help="Path to text or markdown article file")
    parser.add_argument("--out", default="report.csv", help="Output CSV path")
    args = parser.parse_args()

    rubric = load_rubric()

    with open(args.file, "r", encoding="utf-8") as f:
        content = f.read()

    print(f"Evaluating {args.file} against Jev rubric...")
    answers = evaluate_article(content, rubric)

    # Compute composite weighted score deterministically in code (decomposed arithmetic)
    composite_score = 0.0
    row_data = {"file": args.file}

    for dim, conf in rubric["dimensions"].items():
        val = answers[dim].score
        weight = conf["weight"]
        composite_score += val * weight
        row_data[dim] = round(val, 2)

    row_data["composite_score"] = round(composite_score, 2)

    # Attach boolean safety flags
    for flag in rubric["flags"]:
        row_data[flag] = round(answers[flag].value, 3)

    export_to_csv([row_data], args.out)

if __name__ == "__main__":
    main()
```

---

## 6. Implementation Template: Data Center Technician Job Scanner (NYC/NJ)

Applying Kyle's exact Jev architecture to scan and rank Data Center Technician openings (Equinix, Digital Realty, CoreSite, Cyxtera, etc.) into **`APPLY NOW`**, **`STRETCH`**, or **`SKIP`**:

### Job Evaluation Rubric (`dc_job_rubric.json`)
```json
{
  "role": "Data Center Technician",
  "target_region": "New York / New Jersey Metro Area",
  "questions": {
    "tier_ranking": {
      "type": "choice",
      "instructions": "Based on role title, core responsibilities, and experience required, classify this opportunity into exactly one category: 'APPLY NOW' (strong technician fit, accessible entry/mid requirements, active DC hands-on work), 'STRETCH' (senior/lead level, requires extensive specialized engineering or rare certifications), or 'SKIP' (irrelevant role, non-technical, remote software-only, or outside NYC/NJ).",
      "choices": ["APPLY NOW", "STRETCH", "SKIP"]
    },
    "qualification_fit": {
      "type": "score",
      "instructions": "Rate how closely the required skills (racking/stacking, fiber/copper cabling, power/cooling, HVAC, UPS, hardware swap) match a standard Data Center Technician.",
      "scale": [0, 10]
    },
    "recency_signal": {
      "type": "score",
      "instructions": "Rate how recently posted or active this opening appears (10 = posted within 7 days/this week, 0 = stale/closed/30+ days).",
      "scale": [0, 10]
    },
    "is_nyc_nj_metro": {
      "type": "null",
      "instructions": "Is the physical work location confirmed to be in the NYC or Northern New Jersey metro area (e.g. Secaucus, Newark, Piscataway, Weehawken, Manhattan, Clifton, Carteret)?"
    },
    "is_hands_on_hardware": {
      "type": "null",
      "instructions": "Does this role involve direct physical data center floor work (racks, servers, cabling, power) rather than remote virtual support?"
    },
    "requires_security_clearance": {
      "type": "null",
      "instructions": "Does this role strictly mandate an active Top Secret/SCI or DoD security clearance?"
    }
  }
}
```

### Execution Engine (`scan_dc_jobs.py`)
```python
import os
import json
import csv
from typesafe import TypesafeClient

client = TypesafeClient(api_key=os.getenv("TYPESAFE_API_KEY"))

def rank_job_posting(job_text: str, rubric: dict) -> dict:
    """Executes single atomic Jev System 1 evaluation on the job posting."""
    response = client.evaluate(
        model="jev-latest",
        state=job_text,
        questions=rubric["questions"]
    )
    ans = response.answers
    
    tier = ans["tier_ranking"].choice
    confidence = ans["tier_ranking"].confidence
    qual_score = ans["qualification_fit"].score
    recency = ans["recency_signal"].score
    is_local = ans["is_nyc_nj_metro"].value
    is_hands_on = ans["is_hands_on_hardware"].value
    needs_clearance = ans["requires_security_clearance"].value
    
    # Deterministic guardrail rule
    if is_local < 0.5 or is_hands_on < 0.4:
        final_rank = "SKIP"
    else:
        final_rank = tier

    return {
        "final_rank": final_rank,
        "jev_tier": tier,
        "confidence": round(confidence, 3),
        "qual_score": qual_score,
        "recency_score": recency,
        "is_nyc_nj_prob": round(is_local, 3),
        "is_hands_on_prob": round(is_hands_on, 3),
        "clearance_prob": round(needs_clearance, 3),
        "probabilities": ans["tier_ranking"].probabilities
    }
```

---

## 7. Directives for Antigravity Pairing

Whenever performing tasks under this specification:
1. **Never substitute an LLM for Jev decision-making.** When a decision, classification, ranking, or scoring step is needed, construct a Jev question payload.
2. **Decompose Arithmetic:** Let Jev produce probabilities and scores; calculate composite scores, sums, and thresholding in Python code.
3. **Batch Questions in One Request:** Never make multiple sequential calls for questions that evaluate the same state. Bundle them into one atomic payload.
4. **Use Exact Primitives:** Only use `choice`, `score`, and `null`. Do not invent new types.
5. **Always Inspect Probabilities:** Consume the full probability vector (`ans[...].probabilities`) when confidence thresholding is required.
