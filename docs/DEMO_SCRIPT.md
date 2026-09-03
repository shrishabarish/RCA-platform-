# 🎙️ Live Demo Presentation Script (5-Minute & 10-Minute Guide)

Use this step-by-step speaker guide during your live project presentation or viva defense.

---

## ⚡ 5-Minute Quick Pitch Demo

### **[0:00 - 1:00] Introduction & Problem Setup**
1. Open browser at `http://localhost:8000`.
2. **Say:**
   > *"In modern microservices architectures, when an infrastructure component fails, SRE teams are flooded with dozens of symptom alerts across downstream APIs and UIs. Finding the true root cause manually takes upwards of 45 minutes. Today I'm showcasing **CloudLens RCA**, an autonomous incident correlation engine that triages cloud failures in under 200 milliseconds."*

### **[1:00 - 2:30] Live Failure Simulation & Graph Analysis**
1. In the header scenario dropdown, select **⚡ Scenario 1: EC2 Overload & Cascading 500 Outage**.
2. Click **"Simulate Scenario"**.
3. **Say:**
   > *"Notice what just happened. The engine ingested 5 raw cloud events across EC2, API Gateway, and the web application. Our sliding-window correlator grouped them into an active incident. 
   On the left, our dynamic dependency graph immediately highlights the **EC2 node in glowing red** as the root cause, and flags **API** and **Application** in amber as degraded downstream casualties."*

### **[2:30 - 3:45] Explainable Multi-Factor Scoring**
1. Point to the middle **Root Cause Triage & Confidence** panel.
2. **Say:**
   > *"Unlike black-box LLMs, our engine uses deterministic multi-factor mathematical scoring. You can see the explicit breakdown:*
   > *1. **Earliest Temporal Origin:** 25/25 pts — EC2 started the event sequence.*
   > *2. **Dependency Centrality:** 15/30 pts — API depends directly on EC2.*
   > *3. **Downstream Symptom Overlap:** 10/20 pts — Corroborated by actual 500 errors downstream.*
   > *4. **Severity Weight:** 15/20 pts.*
   > *This produces a mathematically verified **65% confidence**."*

### **[3:45 - 4:45] Automated Self-Healing & Live Event Injection**
1. Scroll to the green **Automated Cloud Remediation Runbook** card.
2. **Say:**
   > *"The platform immediately generates the exact AWS CLI auto-scaling command to heal the cluster. If I click **'Execute Auto-Remediation'**, the system applies the fix and resolves the incident status."*
3. Click **"Execute Auto-Remediation"**.
4. Click **"Inject Event"**, choose **DynamoDB**, and submit a custom alarm to show dynamic reactivity!

### **[4:45 - 5:00] Conclusion**
1. **Say:**
   > *"With 16/16 automated tests passing, Docker containerization, and AWS Terraform IaC, CloudLens provides production-ready, autonomous, explainable cloud incident management. Thank you!"*

---

## 🛠️ Quick Commands for the Demo

### Run CLI Engine directly:
```powershell
python run_local.py cascade
python run_local.py db_exhaustion
```

### Run FastAPI Server:
```powershell
python -m uvicorn backend.app.main:app --reload --port 8000
```

### Run Test Suite:
```powershell
python -m pytest
```
