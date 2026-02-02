# smart_trans RAG Knowledge

This knowledge base is used by `traffic_issue_analyzer.py` in RAG mode.
Design goals:
- Deterministic outputs: final `accident_type` and `severity` come from local rules.
- Traceability: RAG retrieves the most relevant notes/rules to attach as evidence.

## Accident Types

### Rear-end (追尾)
Visual cues:
- Two vehicles in same direction, aligned front-to-rear.
- Contact: following vehicle front -> lead vehicle rear.
- Damage: lead rear + following front.

### Side-impact (侧面碰撞)
Visual cues:
- Lateral damage on doors/side panels.
- Vehicles perpendicular/angled (often intersection).
- T-bone patterns.

### Head-on (对向相撞)
Visual cues:
- Two vehicles facing opposite directions with front-front impact.
- Strong frontal deformation.
Notes:
- Often higher severity than rear-end/side-impact.

### Single-vehicle (单车事故)
Visual cues:
- Only one vehicle involved, or one clearly crashed alone.
- Off-road, pole/tree, ditch, or self-spin without another.

### Rollover (翻车)
Visual cues:
- Vehicle on side/roof, wheels up, underbody visible.

### Guardrail collision (撞护栏)
Visual cues:
- Vehicle contacting guardrail/median barrier.
- Deformation aligned with barrier; scrape marks.

### Pedestrian involved (行人事故)
Visual cues:
- Pedestrian near impact area, crosswalk context, emergency aid.
Rule:
- If pedestrian involvement is detected, prioritize this type.

### Non-motor vehicle involved (非机动车事故)
Visual cues:
- Bicycle/e-bike/motorbike involved.
- Bike on ground near vehicle or rider involved.
Rule:
- If both pedestrian and non-motor are present, prioritize pedestrian.

### Multi-vehicle pileup (多车连环)
Visual cues:
- Three or more vehicles with crash involvement.
- Chain collision indicators.

### Road blockage (占道)
Visual cues:
- Vehicles stopped across lanes causing partial/full blockage.
Note:
- Use as auxiliary when collision mode is unclear.

### Wrong-way driving (逆行)
Visual cues:
- Vehicle oriented opposite to traffic flow, wrong direction in lane.
Note:
- Often requires strong scene context; keep confidence conservative.

## Severity Heuristics

### Severe (严重)
Strong indicators (any one is usually enough):
- Rollover.
- Fire / heavy smoke.
- Pedestrian involved.
- Multi-vehicle pileup.
- Major deformation or likely cabin intrusion.
- Full road blockage across multiple lanes.

### Moderate (中等)
Typical indicators:
- Clear collision with moderate damage.
- Two-vehicle crash without severe indicators.
- Partial lane blockage.

### Minor (轻微)
Typical indicators:
- Light scrape/bump, limited damage.
- Vehicles appear movable, stopped on shoulder.
- No vulnerable road users, no rollover/fire.

## Confidence Guidelines

Confidence is derived from evidence strength and consistency:
- Strong cues (rollover/fire/head-on alignment) increase confidence.
- Low resolution, occlusion, or ambiguous scenes reduce confidence.
- If only weak hints (stopped vehicles/traffic jam) without clear collision, keep confidence low.
