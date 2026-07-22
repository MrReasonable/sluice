"""Synthetic application-owned lead notes for the e2e/functional tiers.

One shape, shared by every scenario that seeds a lead already in the application
lifecycle (S2/S4 and the triage never-regress test), so the frontmatter lives in
one place instead of three inline heredocs.
"""
import os

_LEADS_SUBDIR = ("Job Applications", "Job Leads")


def seed_lead_note(vault_dir, *, status, body, company="Example Foundry",
                   role="Staff Engineer", location="Remote",
                   url="https://remoteok.example/jobs/1",
                   applied_date="2026-07-01", ats="example-ats"):
    """Write a synthetic application-owned lead note into
    <vault_dir>/Job Applications/Job Leads/<company> - <role>.md and return its path."""
    leads_dir = os.path.join(vault_dir, *_LEADS_SUBDIR)
    os.makedirs(leads_dir, exist_ok=True)
    path = os.path.join(leads_dir, f"{company} - {role}.md")
    note = (
        "---\n"
        'base: "[[Job Leads.base]]"\n'
        f'company: "{company}"\n'
        f'role: "{role}"\n'
        f'location: "{location}"\n'
        f"status: {status}\n"
        "score: 0\n"
        f'url: "{url}"\n'
        f"applied_date: {applied_date}\n"
        f"ats: {ats}\n"
        'relevance_notes: ""\n'
        "---\n\n"
        f"# {company} - {role}\n\n"
        f"{body}\n"
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(note)
    return path
