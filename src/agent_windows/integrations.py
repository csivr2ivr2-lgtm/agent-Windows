from __future__ import annotations

import importlib.util
import shutil
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class IntegrationStatus:
    component: str
    integration: str
    runtime_cost: str
    default_enabled: bool
    available: bool
    upstream_url: str = ""
    language: str = ""
    status: str = "PENDING"
    execution_path: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def _available(module: str) -> bool:
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ModuleNotFoundError, ValueError):
        return False


def _configured(runtime, *attributes: str) -> bool:
    if runtime is None:
        return False
    settings = getattr(runtime, "settings", None)
    return bool(settings and all(getattr(settings, name, "") for name in attributes))


def integration_matrix(runtime=None) -> tuple[IntegrationStatus, ...]:
    livekit_active = _available("livekit") and _configured(runtime, "livekit_url", "livekit_api_key", "livekit_api_secret")
    needle_active = _available("needle") and bool(runtime is not None and getattr(runtime.settings, "needle_enabled", False))
    openviking_active = _configured(runtime, "openviking_url")
    firecrawl_active = _configured(runtime, "firecrawl_key")
    wigolo_active = bool(runtime is not None and getattr(runtime.settings, "wigolo_url", ""))
    windows_use_active = _available("windows_use")
    unsloth_active = _available("unsloth")
    soup_active = bool(shutil.which("soup") or _available("soup_cli"))

    return (
        IntegrationStatus("llmfit","Python-native hardware/model-fit capability with CLI report","low/on-demand",True,True,"https://github.com/AlexsJones/llmfit","Rust upstream / Python port","ACTIVE","agent_windows.model_fit:model_fit_report"),
        IntegrationStatus("Unsloth","ModelLab job generation and explicitly-approved compatible-host execution","offline/training",False,unsloth_active,"https://github.com/unslothai/unsloth","Python","ACTIVE" if unsloth_active else "CODE_READY","agent_windows.model_lab:ModelLab.prepare/run"),
        IntegrationStatus("Needle","local confidence-gated tool planner; PolicyEngine retains execution authority","~28MB inference/on-demand",True,needle_active,"https://github.com/cactus-compute/needle","Python + native engine","ACTIVE" if needle_active else "CODE_READY","agent_windows.needle_integration:NeedleToolPlanner"),
        IntegrationStatus("Soup","ModelLab YAML generation, dry-run validation, and explicitly-approved training","offline/training",False,soup_active,"https://github.com/MakazhanAlpamys/Soup","Python","ACTIVE" if soup_active else "CODE_READY","agent_windows.model_lab:ModelLab.soup_dry_run/run"),
        IntegrationStatus("LiveKit Agents","realtime AgentSession backend with local session fallback","on-demand realtime",False,livekit_active,"https://github.com/livekit/agents","Python","ACTIVE" if livekit_active else "CODE_READY","agent_windows.livekit_runtime"),
        IntegrationStatus("OpenViking","semantic context tier over durable SQLite source-of-truth","on-demand/network",False,openviking_active,"https://github.com/volcengine/OpenViking","Python/service","ACTIVE" if openviking_active else "CODE_READY","agent_windows.openviking_memory:TieredMemoryStore"),
        IntegrationStatus("Hermes Agent","durable SKILL.md learning/retrieval with guarded skill management tools","low/on-demand",True,True,"https://github.com/NousResearch/hermes-agent","Python behavioral integration","ACTIVE","agent_windows.hermes_skills:HermesSkillStore"),
        IntegrationStatus("OpenHuman","durable thread-goal completion contract injected into AgentLoop budgets/context","low",True,True,"https://github.com/tinyhumansai/openhuman","Rust/TS upstream / Python behavioral port","ACTIVE","agent_windows.openhuman_goals:OpenHumanGoalStore"),
        IntegrationStatus("Ponytail","Python-native minimal-solution plan review and duplicate-call reduction","negligible",True,True,"https://github.com/DietrichGebert/ponytail","JS/plugin upstream / Python port","ACTIVE","agent_windows.ponytail:PonytailReviewer"),
        IntegrationStatus("OmniRoute","Python-native provider scoring: priority/LKGP/latency/cost/quota/network","negligible",True,True,"https://github.com/diegosouzapw/OmniRoute","TypeScript upstream / Python port","ACTIVE","agent_windows.omniroute_policy:OmniRoutePolicy"),
        IntegrationStatus("Prime Agent","bounded Python-native plan-act-observe-verify-recover loop with streaming tools","core",True,True,"https://github.com/PrimeIntellect-ai/prime-agent","TypeScript upstream / Python behavioral port","ACTIVE","agent_windows.agent_loop:AgentLoop"),
        IntegrationStatus("Firecrawl","advanced web search/scrape/crawl adapter behind WebRouter","network/on-demand",False,firecrawl_active,"https://github.com/firecrawl/firecrawl","TypeScript service / Python HTTP adapter","ACTIVE" if firecrawl_active else "CODE_READY","agent_windows.web_tools:FirecrawlAdapter"),
        IntegrationStatus("Wigolo","local-first search/fetch/research backend","local/on-demand",True,wigolo_active,"https://github.com/KnockOutEZ/wigolo","Python/service","CONFIGURED" if wigolo_active else "CODE_READY","agent_windows.web_tools:WigoloAdapter"),
        IntegrationStatus("Microsoft UFO²","primary structured Windows computer-use executor","on-demand",False,bool(runtime is not None and getattr(getattr(runtime,"computer",None),"primary",None)),"https://github.com/microsoft/UFO","Python","CODE_READY","agent_windows.computer_use:UFOExecutor"),
        IntegrationStatus("Windows-Use","UI Automation computer-use fallback","on-demand",False,windows_use_active,"https://github.com/Jeomon/Windows-Use","Python","ACTIVE" if windows_use_active else "CODE_READY","agent_windows.computer_use:WindowsUseExecutor"),
    )


def integrations_report(runtime=None) -> list[dict]:
    return [row.as_dict() for row in integration_matrix(runtime)]


class OptionalBackend:
    def __init__(self, module: str, enabled: bool = False):
        self.module = module
        self.enabled = enabled

    @property
    def healthy(self) -> bool:
        return self.enabled and _available(self.module)

    def require(self):
        if not self.healthy:
            raise RuntimeError(f"optional backend {self.module} is unavailable or disabled")
        return __import__(self.module)
