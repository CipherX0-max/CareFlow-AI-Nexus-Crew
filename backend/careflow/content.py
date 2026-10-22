"""User-facing policy text. Plain language, no legal claims; must be reviewed by the operating organisation before real use."""
CONSENT_VERSION = "2026-10-v1"
CONSENT_TEXT = {
    "version": CONSENT_VERSION,
    "title": "How CareFlow AI uses your information",
    "sections": [
        {"heading": "What we collect", "body": "What you type or say about your symptoms, any reports or photos you upload, your age (or birth year), and the answers you give to follow-up questions."},
        {"heading": "How it is used", "body": "Local software on this system turns your information into a structured summary, notices missing or conflicting details and asks follow-up questions. A fixed set of rules then suggests how soon a member of the care team should look at your case. A person reviews every case and makes the decision."},
        {"heading": "What it is not", "body": "CareFlow AI does not diagnose, prescribe or replace emergency care. It is not monitored in real time. If you think this is an emergency, call your local emergency number or go to the nearest emergency department."},
        {"heading": "Where your data goes", "body": "Your audio, documents and text are processed on this system. They are not sent to an external AI service."},
        {"heading": "Who can see it", "body": "You, and the care-team staff who review cases. Administrators manage accounts and audit logs but cannot open case contents."},
        {"heading": "Your choices", "body": "You can correct any transcription or extracted text before submitting. You can withdraw consent and ask for your case to be deleted; some records may need to be kept for a legally required period."},
        {"heading": "Demonstration notice", "body": "This software is a prototype. Use synthetic (made-up) information unless your organisation has confirmed that real patient data may be used."},
    ],
}
