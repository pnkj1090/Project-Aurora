"""
Data models for the web content extraction subsystem.
Fences extracted content as untrusted passive data.
"""
import html
from datetime import datetime, timezone
from typing import Optional
from pydantic import BaseModel, Field


class ExtractedWebContent(BaseModel):
    """
    Structured extraction result from raw web HTML.
    
    CRITICAL INVARIANT:
    Extracted web content is untrusted PASSIVE DATA. It must never be interpreted
    as application commands, tool execution directives, or system prompt modifications.
    """
    canonical_url: str
    title: Optional[str] = None
    text: str
    character_count: int
    is_truncated: bool = False
    domain: Optional[str] = None
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_fenced_content(
        self,
        source_id: str,
        url: Optional[str] = None,
        title: Optional[str] = None,
    ) -> str:
        """
        Wraps extracted text in structural boundaries to provide data isolation and labeling.
        
        SECURITY ARCHITECTURE:
        - Web content is untrusted data.
        - Structural fencing provides data isolation/labeling and makes the untrusted boundary explicit.
        - Structural fencing is NOT the primary security mechanism for prompt injection.
        - The primary defense remains the instruction hierarchy and server-controlled tool behavior.
        - Structural fencing is a secondary defense that helps clearly separate retrieved web data
          from trusted instructions.
          
        All untrusted attribute values (source_id, url, title, domain) are deterministically
        escaped with standards-compliant HTML/XML entity encoding to prevent attribute termination,
        tag injection, premature closing tags, or structural boundary tampering.
        """
        target_source_id = source_id if source_id is not None else ""
        target_url = url if url is not None else self.canonical_url
        target_title = title if title is not None else self.title

        escaped_source_id = html.escape(str(target_source_id), quote=True)
        attrs = [f'source_id="{escaped_source_id}"']

        if target_url is not None:
            escaped_url = html.escape(str(target_url), quote=True)
            attrs.append(f'url="{escaped_url}"')

        if target_title is not None:
            escaped_title = html.escape(str(target_title), quote=True)
            attrs.append(f'title="{escaped_title}"')

        if self.domain:
            escaped_domain = html.escape(str(self.domain), quote=True)
            attrs.append(f'domain="{escaped_domain}"')

        opening_tag = "<untrusted_web_content " + " ".join(attrs) + ">"
        return f"{opening_tag}\n{self.text}\n</untrusted_web_content>"
