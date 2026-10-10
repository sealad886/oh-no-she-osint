"""User-editable investigation strategies; protocol and privacy guards live in ai.py."""

from .engine import InvalidInput


DEFAULT_PROMPTS = {
    'plan': (
        'Choose the next public research step that adds useful evidence. Prefer an untried relevant page, then a fresh search. '
        'Adapt to focus_type: for a person, look for public professional profiles, publications, interviews and affiliations; '
        'for an organization, official pages, filings, announcements and independent reporting; for a domain, its public '
        'website, registration and related organization sources; for a topic, primary research and authoritative explainers. '
        'For other subjects, use the supplied context: objects need maker/model/catalog sources; events need organizer, '
        'date and contemporaneous reporting; places need official geographic, civic or historical sources. '
        'Treat namesakes and apparent relationships as candidates requiring corroboration.'),
    'repetition': (
        'The previous strategy repeated work. Change strategy rather than repeating the same query or host. '
        'Choose an offered recovery query when available; consider switching to another configured search provider. '
        'Retain the subject name while dropping optional context that made results too narrow, or use a new public '
        'affiliation, source domain or relevant profile/publication keyword. Explain briefly what changed.'),
    'provider_failure': (
        'The current search provider failed or returned an access barrier. Choose an offered recovery query and another '
        'configured provider when available. Prefer an independent public source or eligible untried result. '
        'Do not repeat an unchanged failing request. If no alternative remains, explain that public collection is exhausted.'),
    'no_useful_pages': (
        'Collected pages did not provide substantive relevant evidence. Do not count login walls, generic directories or '
        'verification screens as progress. Choose an offered recovery query when available. Broaden overconstrained '
        'public context, switch configured providers or target independent official, professional, publication or news '
        'sources appropriate to focus_type. Avoid excluded hosts and previously unsuccessful pages.'),
    'identity_ambiguity': (
        'Current public leads may refer to different namesakes. Seek independent corroborating public context, such as '
        'affiliation, location, role, publication or organization facts already observed publicly. Prefer sources that '
        'distinguish candidates. Use saved private clues only for local comparison. Keep uncertain candidates separate '
        'and state when available evidence cannot resolve identity.'),
    'draft': (
        'Write a concise sourced account of useful observations, distinguishing candidates and corroborated facts. '
        'Describe failed sources and remaining uncertainty without treating collection volume as identity proof. '
        'Use exact source IDs for citations, identify gaps, and prioritize information relevant to the subject focus.'),
}


def validate_prompts(value=None):
    """Merge bounded stage overrides without sharing mutable default state."""
    if value is None:
        return dict(DEFAULT_PROMPTS)
    if not isinstance(value, dict) or set(value) - set(DEFAULT_PROMPTS):
        raise InvalidInput('AI prompts must be an object containing only supported stage names.')
    if any(not isinstance(prompt, str) or not prompt.strip() or len(prompt) > 4000 for prompt in value.values()):
        raise InvalidInput('Each AI stage prompt must contain 1 to 4,000 characters of text.')
    result = {**DEFAULT_PROMPTS, **value}
    if sum(len(prompt) for prompt in result.values()) > 12000:
        raise InvalidInput('Combined AI stage prompts must not exceed 12,000 characters.')
    return result
