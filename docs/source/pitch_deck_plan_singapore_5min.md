# HORIZON five-minute competition pitch

## Eight-slide plan for Singapore and Southeast Asia

**Audience:** defence innovation judges, maritime operators, autonomy engineers, and potential design partners  
**Length:** five minutes  
**Core message:** HORIZON supervises high-consequence autonomous systems at runtime, starting with maritime autonomy in Singapore and Southeast Asia.

Use one central message per slide. Keep the pitch to about 600 words. State clearly which results come from the current simulator, which market facts come from external sources, and which product capabilities remain roadmap items.

---

## Slide 1: Problem

**Time:** 55 seconds

### On-slide copy

**HORIZON**  
Runtime assurance for high-consequence autonomy

## Runtime assurance for defence and maritime autonomy

**Autonomy expands operational capability. It also expands the consequence of a wrong decision.**

- Defence and critical-infrastructure systems increasingly depend on sensors, AI perception, communications, and actuators working together in real time.
- In the Singapore Strait on 17 September 2026, the bulk carrier *First Margaux* made contact with the fishing vessel *Luqing Yuanyu 108* in a complex, high-traffic situation. The reported cause remains under investigation.
- Singapore already operates RSN Maritime Security USVs. MPA is expanding USV trials in port waters from the second half of 2026.

**Policy is catching up:** UNCLOS, COLREGs, and the IMO MASS Code.

**The gap:** every platform needs runtime evidence that it can detect degraded inputs, stay inside a safe operating envelope, and recover when a decision is no longer safe.

### Spoken pitch

Autonomy can act faster than an operator can review every decision. That is valuable in defence, but every autonomous mission depends on sensor evidence, AI decisions, communications, and physical actuation. The 17 September collision near Singapore was not an autonomy incident, and investigators have not established its cause. It shows how little tolerance dense waterways leave for late or wrong decisions. Singapore already operates maritime-security USVs while the international framework develops. HORIZON addresses the gap: runtime evidence that a platform can detect degraded inputs, stay safe, and recover.

### Visual

Left: a simple incident timeline with the two vessel names, date, Singapore Strait, and “cause under investigation.”  
Right: a policy stack labelled UNCLOS, COLREGs, IMO MASS Code, then platform-specific assurance.  
Bottom: one bold question: “What checks the AI before it acts?”

### Evidence

- [The Maritime Executive, First Margaux and Luqing Yuanyu 108](https://maritime-executive.com/article/video-chinese-fishing-vessel-survives-dangerous-encounter-with-a-bulker), 20 September 2026. Do not attribute cause, autonomy, fault, casualties, or military involvement beyond confirmed reporting.
- [MINDEF, RSN Maritime Security USVs](https://www.mindef.gov.sg/news-and-events/latest-releases/04feb25_fs/), 4 February 2025.
- [MPA, expanded USV trials](https://www.mpa.gov.sg/media-centre/details/strengthening-maritime-competitiveness-and-operational-excellence), 2026.
- [IMO, autonomous shipping FAQ](https://www.imo.org/en/mediacentre/hottopics/pages/autonomous-shipping.aspx), 2026.
- [IMO, COLREGs overview](https://www.imo.org/en/about/conventions/pages/colreg.aspx).
- [United Nations, UNCLOS overview](https://www.un.org/depts/los/convention_agreements/convention_overview_convention.htm).

---

## Slide 2: Solution

**Time:** 40 seconds

### Title

## HORIZON supervises the AI before it reaches the vessel

### On-slide copy

**Mission autonomy proposes an action. HORIZON checks it. The protected gate controls actuation.**

1. Ingest sensor and system evidence, including camera or radar outputs, AIS, GNSS, IMU, actuator feedback, timing, and communications health.
2. Evaluate the proposed manoeuvre against safety constraints, uncertainty, vessel limits, and available recovery options.
3. Pass, modify, or block the action. When safe, issue a verified recovery command.
4. Log the evidence, decision, issued command, and actual response for review and assurance.

**Current scope:** runtime supervision and safe intervention.  
**Integration roadmap:** connect to perception and computer-vision models, use model and sensor-health signals to identify unreliable outputs, and where validated, restore or reroute a degraded sensor path before escalating control.

### Spoken pitch

HORIZON does not replace the mission AI. It supervises it independently. It observes the evidence available to the autonomy stack, predicts the physical result of a proposed manoeuvre, and decides whether that action remains inside the safety envelope. If it does not, HORIZON can modify or block the command and take the vessel into a verified recovery action. Today our scope is intervention at the command level. As we integrate with computer-vision and other perception stacks, we will add model-health and sensor-health checks. We will only claim sensor correction or restoration after we validate it on real interfaces.

### Visual

Show a single left-to-right chain: sensors and AI perception, mission autonomy, **HORIZON**, protected actuation gate, vessel. Add a feedback line from vessel response back to HORIZON. Use a small callout under HORIZON: “observe, predict, intervene, record.”

---

## Slide 3: How it works

**Time:** 45 seconds

### Title

## From sensor evidence to a safe vessel command

### On-slide copy

**Observe**  
Time-align sensor feeds, AI outputs, network state, and actuator feedback. Preserve uncertainty, missing data, and source lineage.

**Assess**  
Check perception and system-health signals. Flag stale, conflicting, or unreliable evidence before it shapes a manoeuvre.

**Predict**  
Roll the proposed manoeuvre forward through vessel dynamics, traffic motion, actuator lag, and environmental limits.

**Intervene**  
Accept, modify, replace, or block the manoeuvre. Keep a validated recovery path available.

**Verify and record**  
The actuation gate checks command authority and expiry. HORIZON records the inputs, decision, command, and observed vessel response.

### Spoken pitch

The system begins by making the evidence explicit. A camera classification, an AIS message, and a radar contact each have different uncertainty and freshness. HORIZON checks these inputs and the health signals exposed by connected perception systems. It then predicts where the vessel will go if it follows the proposed action. If the manoeuvre cannot retain safe clearance or recovery margin, the supervisor intervenes before the command reaches the vessel. The same chain records what happened so an operator or assessor can trace the decision afterwards.

### Visual

Use a five-stage loop around a vessel silhouette. Draw the “assess” step as a lens over sensor and AI outputs, then show the protected gate immediately before the vessel. Keep the diagram sparse enough to explain in 45 seconds.

---

## Slide 4: Traction and market validation

**Time:** 50 seconds

### Title

## Technical evidence and a growing runtime-assurance market

### On-slide copy

**Current technical traction**

- Closed-loop vessel simulator with noisy public observations, multiple assurance candidates, protected actuation, and an evidence trace.
- We compared alternative architectures behind one interface and selected the strongest current candidate for the demo.
- **Insert verified performance results here:** [scenario count], [collision or safety improvement], [intervention lead time], [false intervention rate], and [evaluation horizon].
- Keep the unprotected-versus-protected trajectory comparison as the main visual.

**External market validation**

- **Exein, Rome:** raised $270M at a $1.7B valuation for embedded runtime cybersecurity for physical-AI devices.
- **Foretellix, Israel and US:** raised an $85M Series C for safety-driven verification and validation of autonomous vehicles.
- **Orca AI, London with a Singapore office:** raised a $72.5M Series B for maritime autonomy and reports more than 1,200 vessels booked and installed.
- **SAIF Autonomy, London:** raised a reported $1.2M pre-seed for runtime-assurance safeguards for physical AI.

**Singapore opportunity:** adjacent companies show demand in Europe, Israel, and the US. HORIZON aims to build a Singapore-based runtime-assurance company for Asian critical infrastructure, beginning with maritime autonomy.

### Spoken pitch

Our first traction is technical. We built a closed-loop vessel simulator, tested multiple assurance architectures behind the same contract, and selected the strongest current candidate. On pitch day, we will show the protected and unprotected trajectories and report the measured performance numbers. The market also validates the category. Companies across Europe, Israel, and the US have raised capital for physical-AI runtime security, autonomy verification, maritime autonomy, and runtime safeguards. We see a gap for a Singapore-based company that applies this discipline to Asian critical infrastructure and maritime operations.

### Visual

Use two-thirds of the slide for the protected-versus-unprotected trajectory and the supplied performance metrics. Use one narrow right column for the four companies, their locations, and a single validated funding or adoption signal. Do not call competitor funding HORIZON traction.

### Evidence

- [Exein funding announcement](https://www.exein.io/blog/exein-raises-270m-at-1-7bn-valuation-to-build-the-security-layer-for-physical-ai), 15 September 2026.
- [Foretellix Series C announcement](https://www.foretellix.com/foretellix-raises-85-million-in-series-c-closing/), 5 December 2023.
- [Orca AI Series B announcement](https://www.orca-ai.io/blog/orca-ai-raises-72-5m-to-advance-autonomous-shipping/), 6 May 2025.
- [Orca AI locations](https://www.orca-ai.io/contact/).
- [SAIF Autonomy funding and runtime-assurance announcement](https://www.rapitasystems.com/news/saif-autonomy-use-rvs-verify-their-groundbreaking-ai-platform), 7 April 2025.

---

## Slide 5: TAM, SAM, SOM

**Time:** 35 seconds

### Title

## Market focus

### On-slide copy

**TAM**  
Runtime assurance for autonomous AI systems in critical infrastructure worldwide, including maritime, logistics, energy, transport, and security operations.

**SAM**  
Southeast Asian operators and platform developers deploying high-consequence autonomy, starting with maritime systems in congested littoral and port environments.

**SOM, first 36 months**  
Two to three paid design partners and one supervised pilot integration.

**Commercial model**  
Integration licence, assurance SDK, validation package, and annual support contract.

### Spoken pitch

The long-term market reaches beyond autonomous vessels. Any critical-infrastructure system that lets AI control physical equipment needs evidence that it can act safely at runtime. Our serviceable market starts in Southeast Asia, where dense waterways, ports, logistics, and critical infrastructure create clear high-consequence use cases. In the first 36 months, we will focus on two or three paid design partners and one supervised pilot. The business model combines an integration licence and SDK with recurring assurance and support contracts.

### Visual

Show three nested circles. Make the TAM broad but short. Put the SOM target and commercial model beside the circles in large type. Do not add a market-value number until the team validates buyer count, pricing, and procurement route.

---

## Slide 6: Roadmap

**Time:** 30 seconds

### Title

## Product and validation roadmap

### On-slide copy

**Today: working demo**  
Closed-loop simulation, multiple assurance architectures, protected actuation, and decision evidence.

**Next 3 to 6 months: market and interface validation**  
Collect structured feedback from judges, operators, and potential design partners during and after the competition. Define the first target sensor and autonomy interfaces. Add verified benchmark metrics.

**Next 6 to 12 months: hardware integration**  
Connect representative cameras, navigation sensors, communications, and actuator interfaces to the HORIZON SDK. Run hardware-in-the-loop tests.

**Then: supervised trials**  
Run approved trials inside a bounded operating envelope with manual recovery and a partner-reviewed assurance case.

### Spoken pitch

Today we have a working demo. Our immediate next step is structured market validation with people we meet at the competition and targeted follow-up interviews. We will use that feedback to choose the first integration partner and sensor interface. Next comes hardware in the loop, then supervised trials with manual recovery inside an approved operating envelope. Each stage has a measurable evidence gate before we progress.

### Visual

Use a four-stage horizontal timeline. Under each stage, add one measurable gate: demo metrics, partner interviews, interface tests, then supervised-trial approval. Add the actual target numbers once the team agrees them.

---

## Slide 7: Why us

**Time:** 30 seconds

### Title

## Team and integration advantage

### On-slide copy

**AI and software engineering**  
Build the runtime-assurance logic, AI and perception interfaces, simulation, verification workflow, and software platform.

**Computer and network engineering**  
Connect HORIZON to cameras, sensors, communications, actuators, and customer hardware. Support deployment, field testing, and system integration.

**One product team**  
We can build the decision layer and connect it to real equipment. This lets us offer an SDK and work directly with customers on their existing autonomy stack.

**Ask**  
Technical review, design-partner introductions, and access to representative sensor or autonomy interfaces for a bounded pilot.

### Spoken pitch

We bring both sides of the product. Our AI and software team builds the runtime-assurance logic outside the mission AI, plus the simulation and verification workflow. Our computer and network engineering team connects that logic to real sensors, communications, and actuators. That mix matters because customers will not replace their whole autonomy stack. They need a team that can integrate an assurance layer into the equipment they already use. We are looking for technical reviewers and design partners to validate the first real interfaces.

### Visual

Use a single team photograph or a clean two-part composition: “AI and software” on one side and “computer and network engineering” on the other. Add only verified individual credentials. End with the partnership ask in one line.

---

## Slide 8: Closing ask

**Time:** 15 seconds

### On-slide copy

**HORIZON**  
Independent runtime assurance for high-consequence autonomy

**We are looking for**

- a technical reviewer;
- a maritime or critical-infrastructure design partner; and
- representative sensor or autonomy interfaces for a bounded pilot.

### Spoken pitch

HORIZON gives autonomous systems an independent safety check before they affect the physical world. We are looking for the partners who can help us validate the first real interfaces and turn this demo into a supervised pilot.

### Visual

Return to the uncrewed vessel image from Slide 1, with the partnership ask in large type. Keep the slide minimal.

---

## Presenter safeguards

- Do not imply the 17 September incident involved autonomous systems, military vessels, a confirmed cause, or established fault. It is a maritime-risk illustration only.
- Present the MASS Code as a non-mandatory commercial-shipping framework that informs HORIZON's assurance baseline. It does not certify defence USVs or provide automatic approval for a military platform.
- Do not claim that the current demo analyses internal neural-network states or repairs sensor feeds unless the implementation and evaluation demonstrate it. Describe this as an integration roadmap capability.
- Do not call competitor funding HORIZON traction. Describe it as external market validation.
- Before presenting Slide 5, replace every bracketed performance placeholder with measured, reproducible results and retain the test conditions.
- Before presenting Slide 8, replace generic team labels with each member's verified role and relevant experience.
