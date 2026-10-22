"""Stand-in for the Gmail adapter during local development: prints notices instead of sending."""


class ConsoleMail:
    def __init__(self):
        self.sent = []

    def send_notice(self, to, subject, body):
        self.sent.append({"to": to, "subject": subject, "body": body})
        print("\n--- ESCALATION NOTICE (not really sent) ---")
        print("To: " + to)
        print("Subject: " + subject)
        print(body)
        print("-------------------------------------------\n")
