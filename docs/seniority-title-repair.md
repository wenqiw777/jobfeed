# Seniority title priority repair — 2026-10-15

## Scope and result
Local source fix only; no commit, push, deployment, or production DB mutation.
Low required experience no longer overrides Senior/Sr, III/3/IV/4, or ownership evidence. SDE/SWE abbreviations are recognized. Existing explicit new-grad paths, neutral MTS, low-year II/2, and mixed lower/senior hiring bands remain eligible. Existing 0–3-year explicit junior-path exception remains unchanged.

## Verification
- Initial regression: 16 failures, 5 passes before repair.
- Mixed-title regression: 7 failures before preserving these bands.
- Final: 113 passed (95 seniority/pipeline checks + 18 hygiene), Ruff passed. Hygiene emits existing size warnings.
- Read-only Mini snapshot: 11,958 historical canonical Apply/Consider evaluations with their stored input JD and source title.
- Final deterministic rule replay: 124 changed from in_scope to out_of_scope; 57 evaluated since October 1 UTC. These are historical evaluation records, not 124 currently visible jobs. No model rerun or live pipeline mutation.
- Initial broad precedence change affected 341 records; restricting the change and preserving mixed bands reduced this to 124.

## Finding dispositions and user-confirmed policy
Fixed: low-year II/2 roles (e.g. Pacific Life 166889, 0–4 years including internships); Boeing Associate/Experienced/Senior 163472 and 160651; Microsoft mixed Engineer/Senior 153486,154709,154862; State Farm mixed Lead/Engineer 155569,157270.

User confirmed that filtering the following roles is correct. These are accepted exclusions, not exceptions to retain. The prior characterization as false positives is withdrawn. Saved JD observations:
- 167477 American Express AI Engineer III: 2+ years; explicitly works under guidance from senior engineers, mentorship. Excluded under the confirmed title policy.
- 157250 Google Software Engineer III: 2 years or 1 with advanced degree; title alone does not establish a senior scope. Other Google III records 165418/165880 also require 2 years.
- 153389 Hubbell Senior Associate Software Engineer: only 1–2 years required; composite Senior Associate title may overstate seniority.
- 146539 Dice Software Engineering Program Manager: 0–2 years including internships/academic experience (preferred section); manager title is not necessarily people management.
- 144142 Datasite Engagement Manager: 0–4 years. Eligibility for the user's software search is separate from seniority.
- 156905/157055/157071 Bluestaq Senior: PhD+0/Master+2/Bachelor+4 alternatives. A viable path depends on candidate education; cannot count PhD+0 as universally junior.

Do not claim a false-positive rate from this targeted review. Production and historical results remain unchanged. User confirmed these title-based exclusions despite low-year requirements. Dice/Jobs via Dice additionally belong in the global blocked publisher list; repaired locally after 3 failing regression cases. Historical Dice evaluations are not proof of current Results visibility.

## Changed records

|Real job ID|Company|Title|Parsed years|Reason|
|---|---|---|---|---|
|15727|snowflake|Engineering Manager, Data Engineering Experience|3|explicit senior ownership|
|79395|MongoDB|Software Engineer 3, Atlas Search Systems|2|explicit seniority title|
|88109|IBM|Confluent - Senior Software Engineer - Backend|2|explicit seniority title|
|88185|Walmart|Senior, Software Engineer - Back End|1|explicit seniority title|
|88576|Qualcomm|Sr.  Backend Engineer - Core Infrastructure|2|explicit seniority title|
|88608|Walmart Global Tech|Senior Software Engineer|1|explicit seniority title|
|91161|Google|Software Engineer III, Access Transparency|2|explicit seniority title|
|92546|Google|Software Engineer III, Home Infrastructure|2|explicit seniority title|
|92553|Google|Software Engineer III|2|explicit seniority title|
|92822|Google|Software Engineer III|2|explicit seniority title|
|92851|Garner Health|Software Engineer III|2|explicit seniority title|
|92931|Google|Software Engineer III, Infrastructure, Core|2|explicit seniority title|
|93214|FastTek Global|Artificial Intelligence Senior Associate|3|explicit seniority title|
|93508|Google|Software Engineer III, AI/ML Engineer|2|explicit seniority title|
|109110|EY|SDC Java Software Developer---Staff/Analyst---St. Louis|1|explicit senior ownership|
|109290|Walmart Global Tech|(USA) Senior, Software Engineer|1|explicit seniority title|
|109577|Ally|Senior Software Engineer|3|explicit seniority title|
|122781|Google|Software Engineer III, Infrastructure, Home Virtualization|2|explicit seniority title|
|122783|Google|Software Engineer III, AI/ML, App Ads Quality|2|explicit seniority title|
|124137|Walmart Global Tech|Senior, Software Engineer - Java|1|explicit seniority title|
|124275|Walmart|(USA) Senior, Software Engineer|1|explicit seniority title|
|124280|Google|Software Engineer III, Infrastructure, Home Virtualization|2|explicit seniority title|
|124281|Walmart|(USA) Senior, Software Engineer|1|explicit seniority title|
|124310|Walmart Global Tech|Senior, Software Engineer|1|explicit seniority title|
|124396|Huntington National Bank|Senior Developer|3|explicit seniority title|
|124398|Huntington National Bank|Senior Developer|3|explicit seniority title|
|124471|SpaceX|Sr. Full Stack Engineer, MES (Manufacturing Execution System)|3|explicit seniority title|
|124496|Walmart Global Tech|Senior, Software Engineer|1|explicit seniority title|
|124618|Zendesk|Senior Fullstack Engineer, Solve|3|explicit seniority title|
|124700|JPMorganChase|Software Engineer III - Data Engineer/Applied AI (LLMs)|3|explicit seniority title|
|126171|AlignityX|Senior Technical Consultant|3|explicit seniority title|
|127027|Microsoft|Software Engineer II & Senior Software Engineer|3|explicit seniority title|
|127564|Unknown|Software Engineer 4 - Media Encoding Pipelines|2|explicit seniority title|
|128988|Unknown|Senior Software Engineer|3|explicit seniority title|
|129819|Zip|Software Engineer, Forward Deployed (Entry - Senior Level)|3|explicit seniority title|
|130267|Unknown|Sr. Engineer, AI Applications|2|explicit seniority title|
|130370|Unknown|Software Engineer III, AI/ML, Core|1|explicit seniority title|
|130518|Unknown|Software Engineer III, Knowledge Catalog For AI|2|explicit seniority title|
|130530|Unknown|Senior Software Engineer - Corporate GenAI|2|explicit seniority title|
|132514|Unknown|Sr. SW AI Engineer|2|explicit seniority title|
|133743|Unknown|Lead AI Engineer|2|explicit senior ownership|
|134697|Penguin Random House|Senior AI Solutions Engineer, Data Science, Penguin Random House (Open to Remote)|3|explicit seniority title|
|134855|Unknown|Senior AI Engineer|2|explicit seniority title|
|134987|State Farm Insurance Companies|Mid-Level/Lead Software Engineer- FULL STACK (Hybrid)|3|explicit senior ownership|
|136601|Opeirex|Senior Software Engineer, Backend (Go/Golang)|3|explicit seniority title|
|136955|NODE|Senior / MTS Product Engineer |3|explicit seniority title|
|138474|LinkedIn|Senior Software Engineer - Video (NYC)|2|explicit seniority title|
|138516|LinkedIn|Senior Software Engineer - Applications|2|explicit seniority title|
|138597|Box|Software Engineer III , Caching|3|explicit seniority title|
|139480|Walmart Global Tech|Senior, Software Engineer|1|explicit seniority title|
|139502|Walmart|(USA) Senior, Software Engineer|1|explicit seniority title|
|139729|University at Buffalo Human Resources|P260204 - Application Development Analyst - Senior Programmer-Analyst|3|explicit seniority title|
|140409|Kelly|Software Engineer IV|3|explicit seniority title|
|140473|Google|Software Engineer III, Infrastructure, YouTube Ads|2|explicit seniority title|
|140959|CHAI AI|Senior Backend Engineer|2|explicit seniority title|
|141006|Walmart Global Tech|(USA) Senior, Software Engineer|1|explicit seniority title|
|141032|PwC|CTIO-AI Engineer-Sr Associate|3|explicit seniority title|
|141074|CHAI AI|Senior Machine Learning Engineer|2|explicit seniority title|
|141402|Walmart Global Tech|(USA) Senior, Software Engineer|1|explicit seniority title|
|142746|JPMorganChase|Software Engineer III - AI Native Development|3|explicit seniority title|
|142773|Jobot|Senior Software Engineer|2|explicit seniority title|
|142900|JPMorganChase|Software Engineer III (Full-Stack)|3|explicit seniority title|
|143232|R1 RCM|Software Engineer III - R37|3|explicit seniority title|
|144142|Datasite|Engagement Manager|0|explicit senior ownership|
|146539|Dice|Software Engineering Program Manager - Video Computer Vision (CV/ML/ALGO)|0|explicit senior ownership|
|149276|Unknown|Solutions Engineer|3|explicit senior ownership|
|149584|Lincoln Financial|Sr Assoc Software Engineer|1|explicit seniority title|
|149723|Garner Health|Software Engineer III|2|explicit seniority title|
|150742|Bain & Company|Senior Full Stack AI Product Engineer|3|explicit seniority title|
|150775|ZoomInfo|Software Engineer III - Developer Productivity|3|explicit seniority title|
|150864|Bain & Company|Senior Full Stack AI Product Engineer|3|explicit seniority title|
|150872|Bain & Company|Senior Full Stack AI Product Engineer|3|explicit seniority title|
|150942|Wizards of the Coast|Senior AI Platform Engineer|2|explicit seniority title|
|150945|Bain & Company|Senior Full Stack AI Product Engineer|3|explicit seniority title|
|150946|Bain & Company|Senior Full Stack AI Product Engineer|3|explicit seniority title|
|150988|Ascendion|Software Engineer III|3|explicit seniority title|
|151356|Curai Health|Senior Software Engineer, Agentic AI|3|explicit seniority title|
|151900|AstraZeneca|Agentic AI Sr. Engineer|3|explicit seniority title|
|152809|JPMorganChase|Software Engineer III (Java/Agentic)|3|explicit seniority title|
|153389|Hubbell Incorporated|Senior Associate Software Engineer - Portsmouth, NH|1|explicit seniority title|
|153835|FortuneCareers.com|Senior Software Engineer|3|explicit seniority title|
|154716|Grainger|Software Engineer III (Java)|3|explicit seniority title|
|154718|Airlines Reporting Corporation (ARC)|Software Engineer III|2|explicit seniority title|
|154728|Aerovy|Senior Software Engineer|3|explicit seniority title|
|154732|Walmart|Senior, Software Engineer|1|explicit seniority title|
|154880|JPMorganChase|Software Engineer III - Asset Management Investment Platform (AMIP)|3|explicit seniority title|
|154914|JPMorganChase|Software Engineer III (Full Stack)|3|explicit seniority title|
|154917|Walmart|Senior, Software Engineer - Python|1|explicit seniority title|
|154962|Walmart|Senior Software Engineer|1|explicit seniority title|
|155050|Saviynt|Senior Principal Software Engineer, AI Onboarding|1|explicit senior ownership|
|155057|Google|Senior Software Engineer, Full Stack|2|explicit seniority title|
|155060|Google|Software Engineer III, Infrastructure, Google Cloud Platforms|2|explicit seniority title|
|155138|Walmart|Senior, Software Engineer|1|explicit seniority title|
|156065|Life.Church|Senior Mobile Software Engineer|3|explicit seniority title|
|156134|EY|Government and Public Sector - Service Delivery Center - AI Solution Senior Developer|3|explicit seniority title|
|156140|EY|Government and Public Sector - Service Delivery Center - AI Solution Senior Developer|3|explicit seniority title|
|156902|Enova International|Senior Software Engineer - Platform (Hybrid)|3|explicit seniority title|
|156905|Bluestaq|Senior Software Engineer|0|explicit seniority title|
|156988|Cloudflare|Senior Software Engineer, Cloudflare for SaaS|3|explicit seniority title|
|157055|Bluestaq|Senior Software Engineer|0|explicit seniority title|
|157071|Bluestaq|Senior Software Engineer|0|explicit seniority title|
|157206|Archer|Senior Software Engineer, Full Stack|3|explicit seniority title|
|157231|V-Soft Consulting Group, Inc.|Software Engineer IV|2|explicit seniority title|
|157250|Google|Software Engineer III, Agentic AI Systems, Cloud Security|1|explicit seniority title|
|157627|MongoDB|Software Engineer 3|2|explicit seniority title|
|157987|PNC|Software Engineer Senior|3|explicit seniority title|
|157991|2K|Senior Software Engineer|3|explicit seniority title|
|158086|Interactive Brokers|Senior Java Engineer|3|explicit seniority title|
|158133|EY|SDC WAMT Senior Backend Engineer---Senior-----Dallas, Raleigh, San Antonio|3|explicit seniority title|
|158590|JPMorganChase|Software Engineer III (Java Full Stack)|3|explicit seniority title|
|158687|Walmart|(USA) Senior, Software Engineer|1|explicit seniority title|
|158739|JPMorganChase|Software Engineer III (Full Stack Java)|3|explicit seniority title|
|158762|Boeing|Greenfield Full Stack Developer (Builder) - Experience or Senior Level|3|explicit seniority title|
|158802|Walmart|(USA) Senior, Software Engineer|1|explicit seniority title|
|158856|Tinder|Software Engineer III, Backend|2|explicit seniority title|
|158891|Tinder|Software Engineer III, Backend|2|explicit seniority title|
|161088|Google|Software Engineer III, Generative AI, Maps|2|explicit seniority title|
|162845|JPMorganChase|Software Engineer III: Full Stack|3|explicit seniority title|
|163872|JPMorganChase|Full-Stack Java/Python React Software Engineer III - Trading Platform|3|explicit seniority title|
|164296|Qualcomm Innovation Center, Inc.|Sr. Engineer, AI Platforms|2|explicit seniority title|
|165418|Google|Software Engineer III- Cloud ISG (multiple openings)|2|explicit seniority title|
|165509|Hired|Senior Software & Digital Tools Development Engineer|3|explicit seniority title|
|165880|Google|Software Engineer III, Platforms and Devices- Play (multiple openings)|2|explicit seniority title|
|167477|American Express|AI Engineer III - Enterprise Architecture|2|explicit seniority title|

## Deployment and backfill
Deployed to Mini on October 15, 2026 Detroit time. Health DB/Redis OK. Backfill updated 219 canonical recommendations to skip (96 Dice, 123 new seniority exclusions, overlap counted as Dice); each persisted verdict and JSON verdict verified. Full before rows and report: data/deployment-backups/title-dice-20261015T221740. No job/application deletion. Local report: artifacts/seniority-title-repair/excluded-jobs.md. No commit or push performed.
