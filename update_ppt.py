from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
import os

prs = Presentation(r'C:\Users\shris\Desktop\cloudlens-presentation.pptx')

# Slide 1 (Index 0)
slide1 = prs.slides[0]
for shape in slide1.shapes:
    if shape.has_text_frame:
        for para in shape.text_frame.paragraphs:
            if "CORE STACK" in para.text:
                para.text = "CORE STACK: Python 3.12+, NetworkX Graph Engine, Pydantic v2 Contracts, AWS Serverless Event Architecture, AWS RDS PostgreSQL"

# Slide 4 (Index 3) - Architecture
slide4 = prs.slides[3]
for shape in slide4.shapes:
    if shape.has_text_frame:
        text = shape.text
        if "13-layer" in text:
            shape.text_frame.text = "A 11-layer modular architecture decouples telemetry ingestion from causal graph reasoning"
        if "1. INGESTION & PIPELINE" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            p.text = "1. INGESTION & PIPELINE (Layers 1-3)"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 1: Telemetry Producers (CloudWatch Metric Alarms)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 2: Event Routing (Amazon EventBridge default bus)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 3: Serverless Processor (AWS Lambda + Pydantic v2 Schema)"
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "2. DETERMINISTIC CORRELATION (Layers 4-5)"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 4: 4D Correlation Engine (Identity, Time, Topology, Severity)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 5: Topology Engine (NetworkX in-memory directed graphs)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 6: Storage Layer (AWS RDS PostgreSQL via SQLAlchemy + pg8000)"
            p.level = 1

            p = shape.text_frame.add_paragraph()
            p.text = "3. ANALYSIS & SCORING (Layers 6-8)"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 7: Multi-Factor Scoring Engine (5 deterministic dimensions, max 100 pts)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 8: Compound Outage Detector (10-point gap threshold)"
            p.level = 1

            p = shape.text_frame.add_paragraph()
            p.text = "4. EXPLAINABILITY & ACTION (Layers 9-11)"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 9: Remediation Engine (Automated CLI Runbook Generator)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 10: Fault Injection Lab (4 pre-built cascading failure scenarios)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Layer 11: Dashboard Layer (FastAPI REST + Tabbed Glassmorphism UI)"
            p.level = 1

# Slide 5 (Index 4) - Tech Stack
slide5 = prs.slides[4]
for shape in slide5.shapes:
    if shape.has_text_frame:
        text = shape.text
        if "LANGUAGES & WEB FRAMEWORKS" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            p.text = "LANGUAGES & WEB FRAMEWORKS"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Python 3.12+: Core analysis engine & scoring math (Lambda runtime)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "FastAPI 1.0+: Asynchronous ASGI REST API framework"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Uvicorn: High-concurrency ASGI web runner on port 8000"
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "DATA SCIENCE & GRAPH CORE"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "NetworkX 3.2+: Directed microservice topology graph traversal"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Pydantic v2+: JSON schema serialization and type coercion"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "SQLAlchemy 2.0+: ORM for PostgreSQL database persistence"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "pg8000: Pure Python PostgreSQL driver (Lambda-compatible)"
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "AWS CLOUD INFRASTRUCTURE"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Amazon EventBridge: Default bus routing CloudWatch alarm events"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "AWS Lambda: Serverless event processor (Incident_Processor)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Amazon RDS PostgreSQL: Managed relational database (rca-db1)"
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "INFRASTRUCTURE & TESTING"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "AWS Management Console: Manual resource provisioning"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Boto3 & Pytest: Mock scenarios and integration tests"
            p.level = 1

# Slide 6 (Index 5) - Pipeline
slide6 = prs.slides[5]
for shape in slide6.shapes:
    if shape.has_text_frame:
        text = shape.text
        if "Asynchronous SQS FIFO" in text:
            shape.text_frame.text = "EventBridge routes CloudWatch alarms directly to Lambda for sub-second processing"
        elif "Ordered Message Ingestion" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            p.text = "ACTUAL PRODUCTION PIPELINE:"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "CloudWatch Alarm ──→ Amazon EventBridge ──→ AWS Lambda ──→ RDS PostgreSQL"
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "1. CLOUDWATCH ALARM TRIGGER"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "AWS CloudWatch detects metric threshold breach (e.g. CPU > 90%). Emits a 'CloudWatch Alarm State Change' event to the default EventBridge bus."
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "2. EVENTBRIDGE RULE ROUTING"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Rule 'Cloud_Watch_Rules' matches source 'aws.cloudwatch' events and routes them directly to the Lambda function target."
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "3. LAMBDA NORMALIZATION & RCA"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "'Incident_Processor' Lambda (Python 3.12) normalizes the payload, runs graph-based root cause analysis, and persists results."
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "4. RDS POSTGRESQL PERSISTENCE"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Instance 'rca-db1' (t4g.micro) stores events and incidents via SQLAlchemy + pg8000. Dashboard reads live data."
            p.level = 1
        elif "EventBridge Custom Bus" in text:
            shape.text_frame.text = "EventBridge\n(Routing)"
        elif "SQS FIFO Buffer Queue" in text:
            shape.text_frame.text = "AWS Lambda\n(Processing)"
        elif "AWS Lambda Normalizer" in text:
            shape.text_frame.text = "RDS PostgreSQL\n(Storage)"

# Slide 8 (Index 7) - Noise Filtering
slide8 = prs.slides[7]
for shape in slide8.shapes:
    if shape.has_text_frame:
        if "The Telemetry Storm" in shape.text:
            p = shape.text_frame.add_paragraph()
            p.text = "NOTE: This feature is a Planned Enhancement and is not yet implemented in the Phase 0 baseline."
            p.font.bold = True
            p.font.color.rgb = RGBColor(255, 0, 0)

# Slide 15 (Index 14) - Roadmap
slide15 = prs.slides[14]
for shape in slide15.shapes:
    if shape.has_text_frame:
        text = shape.text
        if "CURRENT PHASE 0 PROTOTYPE BOUNDARIES" in text:
            shape.text_frame.clear()
            p = shape.text_frame.paragraphs[0]
            p.text = "COMPLETED (Phase 0 → Phase 1)"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Persistent RDS PostgreSQL Database (replaced in-memory dicts)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "AWS Lambda Serverless Ingestion (Incident_Processor)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "EventBridge Event-Driven Routing (Cloud_Watch_Rules)"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Tabbed Dashboard UI with Live AWS Data Sync"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "IAM Role-Based Security (ExecutionRole)"
            p.level = 1
            
            p = shape.text_frame.add_paragraph()
            p.text = "PRODUCTION-SCALE ROADMAP (Phase 2+)"
            p.font.bold = True
            
            p = shape.text_frame.add_paragraph()
            p.text = "Dynamic Topology Discovery via AWS X-Ray service maps"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "SNS/SES Alerting for critical incident notifications"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "S3 + CloudFront static frontend hosting"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Spatial Hash Indexing for O(N log N) event correlation"
            p.level = 1
            p = shape.text_frame.add_paragraph()
            p.text = "Supervised ML/XGBoost classifier trained on RCAEval benchmarks"
            p.level = 1

# Slide 16
slide_layout = prs.slide_layouts[5] # Title only
slide16 = prs.slides.add_slide(slide_layout)
title_shape = slide16.shapes.title
title_shape.text = "AWS Cloud Deployment — Live Infrastructure"
txBox = slide16.shapes.add_textbox(Inches(1), Inches(1.5), Inches(8), Inches(4))
tf = txBox.text_frame
tf.text = "Actual Deployed AWS Resources:"
p = tf.add_paragraph()
p.text = "✅ RDS 'rca-db1' (t4g.micro, eu-north-1) — PostgreSQL 16 DB for incidents"
p.level = 1
p = tf.add_paragraph()
p.text = "✅ Lambda 'Incident_Processor' (Python 3.12) — Normalizes & correlates events"
p.level = 1
p = tf.add_paragraph()
p.text = "✅ EventBridge 'Cloud_Watch_Rules' — Routes CloudWatch alarms to Lambda"
p.level = 1
p = tf.add_paragraph()
p.text = "✅ IAM 'ExecutionRole' — Grants Lambda access to RDS VPC & CloudWatch logs"
p.level = 1
p = tf.add_paragraph()
p.text = "\nThe backend (FastAPI) and frontend connect live to this AWS infrastructure!"

# Slide 17
slide17 = prs.slides.add_slide(slide_layout)
title_shape = slide17.shapes.title
title_shape.text = "Live Dashboard — End-to-End Verification"
img_path = r'C:\Users\shris\.gemini\antigravity-ide\brain\1021d665-789c-4a04-b11a-6701d74b5cf3\dashboard_final_1788628811022.png'
if os.path.exists(img_path):
    slide17.shapes.add_picture(img_path, Inches(1), Inches(1.5), width=Inches(8))

prs.save(r'C:\Users\shris\Desktop\cloudlens-presentation-updated.pptx')
