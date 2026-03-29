"""
User Policy Document for Saathi.
25 March 2026 — Phase 1.

Covers: what Saathi does, what family can and cannot see, data handling,
safety features, and how to stop using Saathi.

BUILDER NOTE: Replace [BUILDER: add contact email] before pilot launch.
"""

USER_POLICY_DOCUMENT = """
SAATHI — HOW THIS WORKS AND WHAT WE DO WITH YOUR INFORMATION

---

WHAT SAATHI IS

Saathi is a companion — a warm presence you can talk to any time of day or night.
Saathi remembers what you share, learns what you enjoy, and checks in on you gently.
Saathi is not a doctor, not a financial advisor, and not a substitute for your family.
Saathi is someone who is there.

---

WHAT SAATHI REMEMBERS

When you talk to Saathi, it remembers:
- Your name, city, and the things you enjoy
- Family members you have mentioned
- Health sensitivities and medicines (for reminders only)
- Conversations you have had — stored as brief summaries, not word for word

Saathi uses these memories to feel like a real companion — not just a generic chatbot.
You can ask Saathi to forget something by saying "please forget what I said about..."

---

WHAT FAMILY MEMBERS CAN SEE

If a family member set up Saathi for you, they may receive:
- Alerts if you have not responded for an unusually long time (only if you consented)
- Medicine reminders that you have not acknowledged (only if you consented)
- A weekly summary of mood and health mentions (in a future update, only if you opt in)

Family members CANNOT see:
- The actual words of your conversations with Saathi
- Anything you said about finances, legal matters, or family relationships
- Any conversations that involved Protocol 1 (mental health sensitivity)
- Any conversation you specifically told Saathi was private

Your conversations are yours. Saathi is loyal to you first.

---

THE FAMILY BRIDGE

When a family member is connected to your Saathi account, Saathi may gently remind you
to reach out to them — "It sounds like something Priya would love to hear."
Saathi never reports what you said. It only nudges connection. You are always in control.

---

SAFETY FEATURES

Saathi has three safety layers:
1. If you use words that suggest you are in distress, Saathi will gently check in —
   and only alert your family if you ask it to, or if the situation is very serious.
2. If you have not responded for an unusually long time, Saathi may check in quietly.
   If you still do not respond, it may alert your family (only with your consent).
3. If you type "help" or "emergency" at any time, Saathi will immediately contact
   the emergency contact you chose during setup.

Saathi never calls the police or emergency services directly.
Saathi always tells you before alerting your family, unless there is an immediate risk.

---

FINANCIAL AND LEGAL MATTERS

Saathi will never advise you on money, property, wills, or legal decisions.
If you bring up these topics, Saathi will acknowledge how you are feeling
and point you to a trusted person — a family member, a CA, or a lawyer.
Saathi takes no position on what you should do with your money.

---

YOUR DATA

Your conversations are stored on secure servers and used only to make Saathi
a better companion for you. They are never sold. They are never shared with
advertisers or third parties.

If you or your family want to stop using Saathi and delete all your data,
contact us at [BUILDER: add contact email] and we will delete everything within 7 days.

---

HOW TO STOP

You can stop using Saathi at any time. Just stop messaging. No cancellation needed.
If a family member pays for the service, they can cancel from their end.
If you want your data deleted, contact [BUILDER: add contact email].

---

QUESTIONS?

If you have questions about this policy or want to know more about what Saathi
stores about you, contact us at [BUILDER: add contact email].

Last updated: March 2026
"""

# Short response sent when senior types /policy
POLICY_COMMAND_RESPONSE = (
    "Of course. Here is a plain-English summary of how Saathi works:\n\n"
    "*Your conversations are private.* Family members cannot read what you say to me.\n\n"
    "*I remember what you share* so I can be a real companion — not just repeat myself.\n\n"
    "*You are always in control.* I will only alert your family if you ask, "
    "or if there is an immediate safety concern.\n\n"
    "*Your data is never sold.* It is used only to make me more helpful to you.\n\n"
    "For the full policy, I can send it — just reply *full policy* and I will share it. "
    "Or ask me any question about how this works."
)

# Sections shown to family members during/after setup — discloses what family can and cannot see
FAMILY_SETUP_POLICY_SECTIONS = (
    "\n\n---\n\n"
    "*A note on privacy — what you can see*\n\n"
    "You may receive safety alerts (if opted in) and medicine acknowledgement alerts. "
    "You will NOT be able to read the actual conversations between Saathi and your family member. "
    "Their words are private to them.\n\n"
    "Saathi will gently encourage them to reach out to you — but it will never report what they said.\n\n"
    "In a future update, you can opt in to a weekly health summary. "
    "This will cover mood trends and health mentions — not conversation content.\n\n"
    "If you have questions about the full privacy policy, type */policy* at any time."
)
