"""
read_web_page tool implementation for Project Aurora.
Read-only, LOW-risk tool enabling safe web page reading via SafeWebReaderService.
"""
import logging
from typing import Any, Dict, Optional

from app.services.tools.base import BaseTool, ToolDefinition, ToolRiskLevel
from app.services.web.privacy.models import OutboundPrivacyViolationError
from app.services.web.reader.service import SafeWebReaderService
from app.services.web.security.exceptions import (
    DNSResolutionError,
    HTTPSDowngradeBlockedError,
    InvalidURLError,
    MaxRedirectsExceededError,
    RequestTimeoutError,
    ResponseSizeLimitExceededError,
    SSRFBlockedError,
    TLSSecurityError,
    URLValidationError,
    WebSecurityError,
)

logger = logging.getLogger("aurora.tools.web.read")


class ReadWebPageTool(BaseTool):
    """
    Assistant tool to safely read public web page contents given an HTTP/HTTPS URL.
    """

    def __init__(self, reader_service: Optional[SafeWebReaderService] = None):
        self.reader_service = reader_service or SafeWebReaderService()

    @property
    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            name="read_web_page",
            description="Safely read the text content of a public web page given its HTTP or HTTPS URL. Returns extracted title, text content, and metadata.",
            parameters_schema={
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "The public HTTP or HTTPS web address to read."
                    }
                },
                "required": ["url"]
            },
            risk_level=ToolRiskLevel.LOW,
            requires_confirmation=False,
            required_permissions=[],
        )

    async def execute(self, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes page read via SafeWebReaderService with server-controlled user identity.
        """
        user_id = params.get("_user_id")
        if not user_id or not isinstance(user_id, str) or not user_id.strip():
            logger.warning("read_web_page invocation missing server-controlled user identity context.")
            return {
                "success": False,
                "error": "Action blocked: User identity context is required."
            }

        url = params.get("url")
        if not url or not isinstance(url, str) or not url.strip():
            return {
                "success": False,
                "error": "Parameter 'url' must be a non-empty string."
            }

        try:
            result = await self.reader_service.read_page(
                url=url.strip(),
                user_id=user_id.strip(),
            )
            return {
                "success": True,
                "url": result.url,
                "title": result.title,
                "domain": result.domain,
                "text": result.text,
                "character_count": result.character_count,
                "is_truncated": result.is_truncated,
                "cached": result.cached,
            }
        except (InvalidURLError, URLValidationError) as ue:
            logger.warning("read_web_page rejected invalid URL: %s", ue)
            return {
                "success": False,
                "error": f"Invalid URL requested: {str(ue)}"
            }
        except SSRFBlockedError as se:
            logger.warning("read_web_page blocked SSRF attempt: %s", se)
            return {
                "success": False,
                "error": "Access denied: The requested URL resolves to a prohibited or private network destination."
            }
        except DNSResolutionError as de:
            logger.warning("read_web_page DNS resolution failed: %s", de)
            return {
                "success": False,
                "error": "Network error: Failed to resolve the requested web host."
            }
        except TLSSecurityError as te:
            logger.warning("read_web_page TLS verification failed: %s", te)
            return {
                "success": False,
                "error": "Security error: Secure TLS connection to the destination host could not be verified."
            }
        except HTTPSDowngradeBlockedError as he:
            logger.warning("read_web_page HTTPS downgrade blocked: %s", he)
            return {
                "success": False,
                "error": "Security error: Insecure HTTP downgrade redirect was blocked."
            }
        except MaxRedirectsExceededError as re:
            logger.warning("read_web_page redirect budget exceeded: %s", re)
            return {
                "success": False,
                "error": "Network error: Too many redirects encountered."
            }
        except ResponseSizeLimitExceededError as se:
            logger.warning("read_web_page size limit exceeded: %s", se)
            return {
                "success": False,
                "error": "Resource limit: The destination web page exceeded the maximum allowed download size."
            }
        except RequestTimeoutError as te:
            logger.warning("read_web_page timed out: %s", te)
            return {
                "success": False,
                "error": "Network error: The connection to the destination host timed out."
            }
        except OutboundPrivacyViolationError as pe:
            logger.warning("read_web_page rejected by privacy policy: %s", pe)
            return {
                "success": False,
                "error": f"Privacy boundary violation: {str(pe)}"
            }
        except WebSecurityError as wse:
            logger.warning("read_web_page security error: %s", wse)
            return {
                "success": False,
                "error": f"Web access security violation: {str(wse)}"
            }
        except Exception as exc:
            logger.exception("Unexpected error in read_web_page: %s", exc)
            return {
                "success": False,
                "error": "Web page retrieval encountered an internal error. Please check the URL and try again."
            }
