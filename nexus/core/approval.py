"""A UI can pause for approval without pretending a tool failed or executed."""


class ApprovalRequired(Exception):
    pass
