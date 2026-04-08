"""Authentication middleware for MaterializationEngine.

Provides FastAPI dependencies for authentication and authorization
using the middle-auth service pattern. Supports OAuth redirect flow
for browser-based access and Bearer token for API clients.

Authentication Flow:
    1. API clients: Send Bearer token in Authorization header
    2. Browser users:
       a. If no token, redirect to auth service for OAuth login
       b. Auth service redirects back with token in query param
       c. Token is converted to secure httponly cookie
       d. Subsequent requests use cookie for auth

Example:
    ```python
    from materialization_engine.api.middleware.auth import (
        require_auth,
        require_permission,
    )

    @router.get("/protected")
    async def protected_route(user: AuthUser = Depends(require_auth)):
        return {"user_id": user.user_id}

    @router.get("/datastack/{datastack_name}")
    async def datastack_route(
        datastack_name: str,
        user: AuthUser = Depends(require_permission("view")),
    ):
        return {"datastack": datastack_name}
    ```
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Annotated, Any
from urllib.parse import quote, urlencode, urlparse, urlunparse, parse_qs

import httpx
import structlog
from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from materialization_engine.config import Settings, get_settings
from materialization_engine.core.errors import AuthServiceError

logger = structlog.get_logger()

security = HTTPBearer(auto_error=False)

# Cookie name for browser-based authentication (matches legacy middle-auth)
TOKEN_COOKIE_NAME = "middle_auth_token"


def is_programmatic_access(request: Request) -> bool:
    """Detect if request is from an API client vs browser navigation.

    Programmatic access is identified by presence of:
    - Authorization header (API clients)
    - X-Requested-With header (AJAX requests)
    - Origin header (CORS requests)

    Browser navigation (direct URL access) has none of these.

    Args:
        request: The incoming request.

    Returns:
        True if programmatic/API access, False if browser navigation.
    """
    auth_header = request.headers.get("authorization")
    xrw_header = request.headers.get("x-requested-with")
    origin_header = request.headers.get("origin")

    return bool(auth_header or xrw_header or origin_header)


def get_authorize_url(settings: Settings, redirect_url: str) -> str:
    """Construct the OAuth authorization URL.

    Args:
        settings: Application settings with auth service URL.
        redirect_url: URL to redirect back to after authentication.

    Returns:
        Full authorization URL with redirect parameter.
    """
    # Use the auth service URL, ensuring it has the correct format
    auth_url = settings.auth.service_url.rstrip("/")
    return f"{auth_url}/api/v1/authorize?redirect={quote(redirect_url)}"


def get_url_without_token(url: str) -> str:
    """Remove token query parameters from URL.

    Used after converting query param token to cookie to prevent
    token from appearing in browser history/bookmarks.

    Args:
        url: Original URL that may contain token params.

    Returns:
        URL with token parameters removed.
    """
    parsed = urlparse(url)
    query_params = parse_qs(parsed.query, keep_blank_values=True)

    # Remove token-related params
    query_params.pop(TOKEN_COOKIE_NAME, None)
    query_params.pop("token", None)

    # Rebuild query string (parse_qs returns lists, need to flatten)
    new_query = urlencode(
        {k: v[0] if len(v) == 1 else v for k, v in query_params.items()},
        doseq=True,
    )

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment,
    ))


def create_token_cookie_response(redirect_url: str, token: str) -> Response:
    """Create redirect response that sets token as secure cookie.

    After OAuth callback, this converts the query param token to a
    secure httponly cookie and redirects to clean URL.

    Args:
        redirect_url: URL to redirect to (without token in query).
        token: The authentication token to set as cookie.

    Returns:
        RedirectResponse with Set-Cookie header.
    """
    response = RedirectResponse(url=redirect_url, status_code=302)
    response.set_cookie(
        key=TOKEN_COOKIE_NAME,
        value=token,
        secure=True,
        httponly=True,
        samesite="lax",
    )
    return response


@dataclass
class AuthUser:
    """Authenticated user information.

    Attributes:
        user_id: Unique user identifier (email or sub).
        email: User email address.
        name: User display name.
        groups: List of groups the user belongs to.
        permissions: Dict of resource -> permission levels.
        is_admin: Whether user has admin privileges (admin or superadmin role).
        token: The original auth token.
        expires_at: Token expiration time.
    """

    user_id: str
    email: str
    name: str = ""
    groups: list[str] = field(default_factory=list)
    permissions: dict[str, list[str]] = field(default_factory=dict)
    is_admin: bool = False
    token: str = ""
    expires_at: datetime | None = None

    def has_permission(self, resource: str, permission: str) -> bool:
        """Check if user has a specific permission on a resource.

        Args:
            resource: The resource name (e.g., datastack name).
            permission: The permission level (view, edit, admin).

        Returns:
            True if user has the permission.
        """
        # Admins have all permissions
        if self.is_admin:
            return True
        resource_perms = self.permissions.get(resource, [])
        return permission in resource_perms or "admin" in resource_perms

    def in_group(self, group: str) -> bool:
        """Check if user is a member of a group.

        Args:
            group: The group name to check.

        Returns:
            True if user is in the group.
        """
        return group in self.groups

    def shares_group_with(self, required_groups: list[str]) -> bool:
        """Check if user shares any group with the required groups.

        Args:
            required_groups: List of groups that grant access.

        Returns:
            True if user is in at least one of the required groups.
        """
        if self.is_admin:
            return True
        return bool(set(self.groups) & set(required_groups))


class AuthClient:
    """Client for middle-auth service.

    Handles token validation and permission checking against
    the middle-auth authentication service.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize auth client.

        Args:
            settings: Application settings containing auth configuration.
        """
        self.settings = settings
        self._http_client: httpx.AsyncClient | None = None

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client."""
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(
                base_url=self.settings.auth.service_url,
                timeout=httpx.Timeout(30.0),
            )
        return self._http_client

    async def close(self) -> None:
        """Close HTTP client."""
        if self._http_client is not None:
            await self._http_client.aclose()
            self._http_client = None

    async def validate_token(self, token: str) -> AuthUser:
        """Validate an authentication token with the auth service.

        Args:
            token: Bearer token to validate.

        Returns:
            AuthUser with user information.

        Raises:
            AuthServiceError: If validation fails.
            HTTPException: If token is invalid (401).
        """
        try:
            # Use /api/v1/user/cache endpoint (matches middle-auth client)
            response = await self.http_client.get(
                "/api/v1/user/cache",
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 401:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail="Invalid or expired token",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            if response.status_code != 200:
                logger.warning(
                    "Auth service returned error",
                    status_code=response.status_code,
                    response=response.text[:200],
                )
                raise AuthServiceError(
                    f"Auth service returned {response.status_code}",
                    status_code=response.status_code,
                )

            data = response.json()

            return AuthUser(
                user_id=data.get("id", data.get("sub", "")),
                email=data.get("email", ""),
                name=data.get("name", ""),
                groups=data.get("groups", []),
                permissions=data.get("permissions", {}),
                is_admin=data.get("admin", False) or data.get("superadmin", False),
                token=token,
                expires_at=datetime.fromtimestamp(data["exp"], tz=UTC) if "exp" in data else None,
            )

        except httpx.RequestError as e:
            logger.error("Failed to contact auth service", error=str(e))
            raise AuthServiceError(
                "Failed to contact authentication service",
                original_error=str(e),
            ) from e

    async def check_permission(
        self,
        token: str,
        resource: str,
        permission: str,
    ) -> bool:
        """Check if user has permission on a resource.

        Args:
            token: Bearer token.
            resource: Resource name (e.g., datastack name).
            permission: Required permission level.

        Returns:
            True if user has permission.

        Raises:
            AuthServiceError: If check fails.
        """
        try:
            response = await self.http_client.get(
                f"/api/v1/permission/{resource}/{permission}",
                headers={"Authorization": f"Bearer {token}"},
            )

            if response.status_code == 200:
                return True
            elif response.status_code in (401, 403):
                return False
            else:
                logger.warning(
                    "Permission check failed",
                    resource=resource,
                    permission=permission,
                    status_code=response.status_code,
                )
                return False

        except httpx.RequestError as e:
            logger.error(
                "Failed to check permission",
                resource=resource,
                permission=permission,
                error=str(e),
            )
            raise AuthServiceError(
                "Failed to check permission",
                resource=resource,
                permission=permission,
            ) from e


_auth_client: AuthClient | None = None


def get_auth_client(settings: Settings = Depends(get_settings)) -> AuthClient:
    """Get the auth client instance.

    Uses a module-level singleton for connection reuse.
    """
    global _auth_client
    if _auth_client is None:
        _auth_client = AuthClient(settings)
    return _auth_client


@dataclass
class ExtractedToken:
    """Result of token extraction from request.

    Attributes:
        token: The extracted token, or None if not found.
        from_query_param: True if token was from query parameter.
    """

    token: str | None
    from_query_param: bool = False


def _extract_token(request: Request, credentials: HTTPAuthorizationCredentials | None) -> ExtractedToken:
    """Extract auth token from request using multiple methods.

    Token extraction priority (matches legacy middle-auth behavior):
    1. Authorization header (Bearer token) - highest priority
    2. Query parameter (middle_auth_token) - OAuth callback
    3. Cookie (middle_auth_token) - for browser requests

    Args:
        request: The incoming request.
        credentials: HTTPBearer credentials if present.

    Returns:
        ExtractedToken with token and source information.
    """
    # 1. Check Authorization header (Bearer token) - highest priority
    if credentials is not None:
        return ExtractedToken(token=credentials.credentials, from_query_param=False)

    # 2. Check query parameter (OAuth callback)
    query_token = request.query_params.get(TOKEN_COOKIE_NAME)
    if query_token:
        return ExtractedToken(token=query_token, from_query_param=True)

    # 3. Check cookie (for browser-based access)
    cookie_token = request.cookies.get(TOKEN_COOKIE_NAME)
    if cookie_token:
        return ExtractedToken(token=cookie_token, from_query_param=False)

    return ExtractedToken(token=None, from_query_param=False)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    settings: Settings = Depends(get_settings),
) -> AuthUser | None:
    """Extract and validate current user from request.

    Supports multiple authentication methods:
    - Bearer token in Authorization header (for API clients)
    - Query parameter token (OAuth callback)
    - Cookie-based token (for browser access, matches legacy middle-auth)

    Returns None if no auth is provided or auth is disabled.
    """
    if not settings.auth.enabled:
        return AuthUser(
            user_id="anonymous",
            email="anonymous@disabled",
            name="Anonymous",
            groups=["public"],
            permissions={},
        )

    extracted = _extract_token(request, credentials)
    if extracted.token is None:
        return None

    auth_client = get_auth_client(settings)
    return await auth_client.validate_token(extracted.token)


async def require_auth(
    user: AuthUser | None = Depends(get_current_user),
) -> AuthUser:
    """Dependency that requires authentication.

    Use as a FastAPI dependency to protect routes.

    Raises:
        HTTPException: 401 if not authenticated.
    """
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


def require_permission(
    permission: str,
    resource_param: str = "datastack_name",
) -> Callable[..., Any]:
    """Create a dependency that requires a specific permission.

    Args:
        permission: Required permission level (view, edit, admin).
        resource_param: Path parameter name containing the resource.

    Returns:
        FastAPI dependency function.

    Example:
        ```python
        @router.get("/datastack/{datastack_name}")
        async def get_datastack(
            datastack_name: str,
            user: AuthUser = Depends(require_permission("view")),
        ):
            ...
        ```
    """

    async def permission_dependency(
        request: Request,
        user: AuthUser = Depends(require_auth),
        settings: Settings = Depends(get_settings),
    ) -> AuthUser:
        resource = request.path_params.get(resource_param)
        if resource is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Missing path parameter: {resource_param}",
            )

        if not settings.auth.enabled:
            return user

        auth_client = get_auth_client(settings)
        has_perm = await auth_client.check_permission(
            user.token,
            resource,
            permission,
        )

        if not has_perm:
            logger.warning(
                "Permission denied",
                user_id=user.user_id,
                resource=resource,
                permission=permission,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Permission '{permission}' required on '{resource}'",
            )

        return user

    return permission_dependency


RequireAuth = Annotated[AuthUser, Depends(require_auth)]


async def require_admin(
    user: AuthUser = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    """Dependency that requires admin privileges.

    Use on workflow trigger endpoints and other admin-only operations.
    Accepts users with either 'admin' or 'superadmin' role.

    Raises:
        HTTPException: 403 if user is not an admin.
    """
    if not settings.auth.enabled:
        return user

    if not user.is_admin:
        logger.warning(
            "Admin access denied",
            user_id=user.user_id,
            email=user.email,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin privileges required",
        )
    return user


def require_group(
    *groups: str,
) -> Callable[..., Any]:
    """Create a dependency that requires membership in at least one group.

    Args:
        groups: One or more group names that grant access.

    Returns:
        FastAPI dependency function.

    Example:
        ```python
        @router.get("/data")
        async def get_data(
            user: AuthUser = Depends(require_group("researchers", "admins")),
        ):
            ...
        ```
    """

    async def group_dependency(
        user: AuthUser = Depends(require_auth),
        settings: Settings = Depends(get_settings),
    ) -> AuthUser:
        if not settings.auth.enabled:
            return user

        if not user.shares_group_with(list(groups)):
            logger.warning(
                "Group access denied",
                user_id=user.user_id,
                user_groups=user.groups,
                required_groups=groups,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Membership in one of these groups required: {', '.join(groups)}",
            )
        return user

    return group_dependency


async def require_datastack_access(
    request: Request,
    user: AuthUser = Depends(require_auth),
    settings: Settings = Depends(get_settings),
) -> AuthUser:
    """Dependency that requires access to a datastack via group membership.

    Checks if user is in any group associated with the datastack.
    The datastack name is extracted from the path parameter 'datastack_name'.

    For read-only access, users need to be in a group that has access to the datastack.
    """
    if not settings.auth.enabled:
        return user

    datastack_name = request.path_params.get("datastack_name")
    if datastack_name is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing datastack_name path parameter",
        )

    # Admins have access to all datastacks
    if user.is_admin:
        return user

    # Check if user has any permission on this datastack
    if datastack_name in user.permissions:
        return user

    # Check if user is in a group that matches the datastack
    # Common pattern: datastack name is used as a group name
    if user.in_group(datastack_name):
        return user

    logger.warning(
        "Datastack access denied",
        user_id=user.user_id,
        datastack=datastack_name,
        user_groups=user.groups,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=f"Access to datastack '{datastack_name}' denied",
    )


# Type aliases for dependency injection
RequireAdmin = Annotated[AuthUser, Depends(require_admin)]
RequireDatastackAccess = Annotated[AuthUser, Depends(require_datastack_access)]