"""
AI Service Module for Refitd

Provides AI-powered features using OpenAI:
- ReFitd canonical item tagging (structured tags with confidence)
- Style tag generation from product images
- Semantic search with embeddings
- Conversational assistant

Configuration:
- Set OPENAI_API_KEY in .env file

OpenAI Setup:
- Sign up at https://platform.openai.com
- Get API key from dashboard
- Add to .env: OPENAI_API_KEY=sk-...
"""

# Import OpenAI client
try:
    from .openai_client import OpenAIClient, OpenAIConfig

    OPENAI_AVAILABLE = True
except ImportError:
    OpenAIClient = None
    OpenAIConfig = None
    OPENAI_AVAILABLE = False

from .chat import ChatAssistant
from .embeddings import EmbeddingsService

# Import services - both old and new taggers
from .style_tagger import StyleTagger

# Import ReFitd canonical tagger (new structured tagging system)
try:
    from .refitd_tagger import AITagOutput, ReFitdTagger, ReFitdTaggerConfig
    from .tag_policy import (
        apply_tag_policy,
        apply_tag_policy_batch,
        CanonicalTags,
        merge_composition_into_tags_final,
        POLICY_VERSION,
        PolicyResult,
        PolicyThresholds,
    )

    REFITD_TAGGER_AVAILABLE = True
except ImportError:
    ReFitdTagger = None
    ReFitdTaggerConfig = None
    AITagOutput = None
    apply_tag_policy = None
    apply_tag_policy_batch = None
    PolicyResult = None
    CanonicalTags = None
    merge_composition_into_tags_final = None
    PolicyThresholds = None
    POLICY_VERSION = None
    REFITD_TAGGER_AVAILABLE = False

__all__ = [
    # Clients
    "OpenAIClient",
    "OpenAIConfig",
    # Services
    "StyleTagger",
    "EmbeddingsService",
    "ChatAssistant",
    # ReFitd Canonical Tagger
    "ReFitdTagger",
    "ReFitdTaggerConfig",
    "AITagOutput",
    "apply_tag_policy",
    "apply_tag_policy_batch",
    "PolicyResult",
    "CanonicalTags",
    "merge_composition_into_tags_final",
    "PolicyThresholds",
    "POLICY_VERSION",
    # Availability flags
    "OPENAI_AVAILABLE",
    "OLLAMA_AVAILABLE",
    "REFITD_TAGGER_AVAILABLE",
]
