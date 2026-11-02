"""Local capability inspection and saved source observations; never live probes.

Prerequisite availability permits an attempt, not a claim that a provider works.
Runtime states are recorded snapshots, not process or service health checks.
"""

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil

from .ai import cached_model
from .documents import capabilities
from .workspace import Workspace

HEALTH_MAX_AGE_SECONDS = 24 * 60 * 60


def _timestamp(value):
    try:
        parsed = datetime.fromisoformat(value)
        return parsed.astimezone(timezone.utc) if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def source_readiness(item, *, allow_network=True, observation=None,
                     prerequisites_ready=True, current=None):
    """Project trusted metadata and one saved observation into an attempt status."""
    current = current or datetime.now(timezone.utc)
    observed = observation or {}
    checked = _timestamp(observed.get('updated_at'))
    retry = _timestamp(observed.get('retry_at'))
    stale = bool(checked and (current - checked).total_seconds() > HEALTH_MAX_AGE_SECONDS)
    observed_state = observed.get('state')
    health_state = ('cooldown' if retry and retry > current else 'stale' if stale else
                    'last_success' if observed_state in {'ready', 'success'} else
                    observed_state if observed_state in {'blocked', 'rate_limited', 'uninterpretable', 'error', 'unavailable'} else
                    'unverified')
    health_messages = {
        'unverified': 'No source check is recorded for this case.',
        'last_success': 'The last recorded operation succeeded; current availability is not verified.',
        'stale': 'The last source observation is more than 24 hours old.',
        'cooldown': 'A recorded source cooldown is still active.',
        'blocked': 'The last operation encountered an access block.',
        'rate_limited': 'The last operation was rate limited; no active cooldown remains.',
        'uninterpretable': 'The last response could not be interpreted.',
        'error': 'The last operation failed.',
        'unavailable': 'The source was unavailable during the last operation.',
    }
    network_required = bool(item.get('max_requests'))
    permitted = bool(allow_network) if network_required else True
    state = ('needs_setup' if not prerequisites_ready else 'offline' if not permitted else
             'cooldown' if health_state == 'cooldown' else 'unverified' if network_required else 'available')
    message = ('Install or configure this source prerequisite separately.' if state == 'needs_setup' else
               'External collection is disabled for this process.' if state == 'offline' else
               health_messages['cooldown'] if state == 'cooldown' else
               'Implementation is available; provider reachability has not been checked.' if network_required else
               'Local implementation is available; evidence input is validated when run.')
    return {
        'id': item['id'], 'label': item['label'], 'implementation_available': True,
        'prerequisites_ready': bool(prerequisites_ready), 'network_required': network_required,
        'network_permitted': permitted, 'available': prerequisites_ready and permitted and state != 'cooldown',
        'state': state, 'message': message,
        'health': {'state': health_state, 'observed_state': observed_state,
                   'checked_at': checked.isoformat() if checked else None,
                   'retry_at': retry.isoformat() if retry else None,
                   'stale': stale, 'message': health_messages[health_state]},
    }


def _model_component(ai=None):
    binary = bool(shutil.which('llama-server'))
    model = ai.model_path if ai is not None and ai.model_path is not None else cached_model()
    valid_model = False
    if model is not None:
        try:
            path = Path(model)
            if path.is_file() and path.stat().st_size > 4:
                with path.open('rb') as stream:
                    valid_model = stream.read(4) == b'GGUF'
        except OSError:
            pass
    runtime_state = 'not_running'
    if ai is not None:
        with ai._lock:
            runtime_state = ai._state
    state = 'available' if binary and valid_model else 'needs_setup'
    return {'id': 'local_ai', 'label': 'Optional local AI', 'state': state,
            'prerequisites_ready': binary and valid_model, 'runtime_installed': binary,
            'model_available': valid_model, 'runtime_state': runtime_state,
            'message': 'Local prerequisites are present. Runtime state is recorded, not probed.' if state == 'available' else
                       'Install llama.cpp and cache a valid GGUF separately; no download or startup is performed.'}


def _search_component(engine, service=None):
    binary = bool(shutil.which('docker'))
    state, runtime_state, readable = {}, 'not_checked', True
    if service is not None:
        # status() inspects Docker and probes HTTP. Read only its local metadata.
        if service.lock.acquire(blocking=False):
            try:
                state = dict(service.state)
                runtime_state = service.phase or ('stopping' if service.stopping else
                                                 'error' if service.last_error else 'not_checked')
            finally:
                service.lock.release()
        else:
            readable, runtime_state = False, 'busy'
    else:
        directory = engine.directory / 'search-service'
        path = directory / 'state.json'
        try:
            if directory.is_symlink() or path.is_symlink():
                readable = False
            elif path.exists():
                with path.open('rb') as stream:
                    raw = stream.read(16385)
                state = json.loads(raw) if len(raw) <= 16384 else None
                if not isinstance(state, dict):
                    state, readable = {}, False
        except (OSError, ValueError):
            readable = False
    configured = bool(re.fullmatch(r'sha256:[a-f0-9]{64}', str(state.get('image_id', ''))))
    ready = binary and configured and readable
    return {'id': 'search_service', 'label': 'Optional local metasearch',
            'state': 'unverified' if ready else 'needs_setup',
            'prerequisites_ready': ready, 'docker_installed': binary,
            'configured': configured, 'owned_container': bool(state.get('container')),
            'runtime_state': runtime_state,
            'message': 'Pinned setup is recorded; Docker, container ownership and HTTP health were not checked.' if ready else
                       'Use the explicit metasearch setup controls after installing Docker separately.' if readable else
                       'Local setup metadata could not be inspected. Use the metasearch controls to inspect or recover it.'}


def report(engine, *, case_id=None, allow_network=False, ai=None, search_service=None):
    """Inspect local files and saved state without constructing runtime managers."""
    from .connectors import REGISTRY
    current = datetime.now(timezone.utc)
    observations = {row['connector']: row for row in Workspace(engine).health(case_id)} if case_id else {}
    document = capabilities()
    documents_ready = all(document['tools'].values())
    document_component = {'id': 'documents', 'label': 'Local document extraction',
                          'state': 'available' if documents_ready else 'partial',
                          'prerequisites_ready': documents_ready, **document,
                          'message': 'Local extraction tools are installed; no binaries were executed.' if documents_ready else
                                     'Office and text import remain available. Install missing Poppler/Tesseract tools separately for PDF/OCR.'}
    model_component = _model_component(ai)
    search_component = _search_component(engine, search_service)
    sources = [source_readiness(item, allow_network=allow_network,
                               observation=observations.get(item['id']), current=current,
                               prerequisites_ready=search_component['prerequisites_ready'] if item['id'] == 'searxng' else True)
               for item in REGISTRY.values()]
    return {'schema_version': 1, 'checked_at': current.isoformat(),
            'network_permitted': bool(allow_network), 'case_scoped': bool(case_id),
            'components': [document_component, model_component, search_component], 'sources': sources}
