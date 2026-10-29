"""Write-only personal model credentials; fixed upstream provider endpoints."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, SecretStr, Field
from apps.api.auth import current_user_id
from apps.api.integrations import product_providers as providers
from apps.api.jobs.store import get_store

router = APIRouter()


class KeyBody(BaseModel):
    api_key: SecretStr = Field(min_length=8, max_length=4096)


@router.get('/keys/providers')
def statuses(user_id: str = Depends(current_user_id)):
    return {'providers': [dict(providers.key_status(user_id, p), label=label)
                          for p, label in providers.EXTRA_PROVIDERS.items()],
            'administrator_providers': [dict(provider=p, label=label,
                                           configured=providers.server_provider_enabled(p))
                                       for p, label in providers.ADMIN_PROVIDERS.items()]}


@router.put('/keys/providers/{provider}')
def save(provider: str, body: KeyBody, user_id: str = Depends(current_user_id)):
    try:
        providers.save_key(user_id, provider, body.api_key.get_secret_value())
        return providers.key_status(user_id, provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.delete('/keys/providers/{provider}')
def delete(provider: str, user_id: str = Depends(current_user_id)):
    try:
        providers._known(provider)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {'deleted': get_store().delete_user_key(user_id, 'provider:' + provider)}
