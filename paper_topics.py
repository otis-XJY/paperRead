"""Controlled primary-topic taxonomy for archived paper notes.

The values are a focused runtime projection of
``work/LLM_Agent_MultiAgent_Literature_Taxonomy.md``.  A paper receives one
primary topic only; broad discovery categories remain unchanged.
"""

import re

TOPICS_BY_CATEGORY = {
    "UAV_VLN": [
        "Embodied_Agent.VisionLanguageGrounding",
        "Embodied_Agent.SpatialRepresentation",
        "Embodied_Agent.NavigationPlanning",
        "Embodied_Agent.NavigationMemory",
        "Embodied_Agent.WorldModelNavigation",
        "Embodied_Agent.ActivePerception",
        "Embodied_Agent.UAVSpecificNavigation",
        "Embodied_Agent.GeneralizationTransfer",
        "Embodied_Agent.EfficientEmbodiedAgent",
    ],
    "multi_VLN": [
        "Multi_Embodied_Agent.CollaborativeNavigation",
        "Multi_Embodied_Agent.CooperativePerception",
        "Multi_Embodied_Agent.SharedMapping",
        "Multi_Embodied_Agent.CollaborativePlanning",
        "Multi_Embodied_Agent.TaskTargetAllocation",
        "Multi_Embodied_Agent.EmbodiedCommunication",
        "Multi_Embodied_Agent.HeterogeneousRobotTeams",
        "Multi_Embodied_Agent.SharedSkill",
        "Multi_Embodied_Agent.SharedWorldModel",
    ],
    "MultiAgent_Game_Theory": [
        "MultiAgent_GameTheory.GameModels",
        "MultiAgent_GameTheory.Equilibrium",
        "MultiAgent_GameTheory.OnlineLearningInGames",
        "MultiAgent_GameTheory.Cooperation",
        "MultiAgent_GameTheory.NegotiationBargaining",
        "MultiAgent_GameTheory.MechanismIncentiveDesign",
        "MultiAgent_GameTheory.LLMxGameTheory",
    ],
    "MARL": [
        "MARL.LearningParadigm",
        "MARL.ValueBasedMARL",
        "MARL.PolicyBasedMARL",
        "MARL.CreditAssignment",
        "MARL.Communication",
        "MARL.CoordinationStructure",
        "MARL.PartnerTeammateModeling",
        "MARL.GeneralizationCoordination",
        "MARL.OfflineModelBasedMARL",
        "MARL.ExplorationCurriculum",
        "MARL.PartialObservability",
        "MARL.Scaling",
    ],
    "LLM_Agent_Memory_Tool_Skill": [
        "LLM_Agent.Memory.WorkingMemory",
        "LLM_Agent.Memory.EpisodicMemory",
        "LLM_Agent.Memory.SemanticMemory",
        "LLM_Agent.Memory.ProceduralMemory",
        "LLM_Agent.Memory.ProspectiveMemory",
        "LLM_Agent.Memory.ExternalMemory",
        "LLM_Agent.Memory.Acquisition",
        "LLM_Agent.Memory.Retrieval",
        "LLM_Agent.Memory.Consolidation",
        "LLM_Agent.Memory.Compression",
        "LLM_Agent.Experience.Reflection",
        "LLM_Agent.Experience.Abstraction",
        "LLM_Agent.Skill.Discovery",
        "LLM_Agent.Skill.BoundaryDiscovery",
        "LLM_Agent.Skill.Abstraction",
        "LLM_Agent.Skill.Representation",
        "LLM_Agent.Skill.ExecutionSelection",
        "LLM_Agent.Skill.Composition",
        "LLM_Agent.Skill.LibraryManagement",
        "LLM_Agent.Skill.Transfer",
        "LLM_Agent.Tool.Understanding",
        "LLM_Agent.Tool.RetrievalSelection",
        "LLM_Agent.Tool.UsePlanning",
        "LLM_Agent.Tool.Execution",
        "LLM_Agent.Tool.Learning",
        "LLM_Agent.Tool.DiscoveryCreation",
    ],
    "LLM_Agent_Self_Evolution": [
        "LLM_Agent.SelfEvolution.PromptEvolution",
        "LLM_Agent.SelfEvolution.MemoryEvolution",
        "LLM_Agent.SelfEvolution.RuleEvolution",
        "LLM_Agent.SelfEvolution.SkillEvolution",
        "LLM_Agent.SelfEvolution.ToolEvolution",
        "LLM_Agent.SelfEvolution.WorkflowEvolution",
        "LLM_Agent.SelfEvolution.ArchitectureEvolution",
        "LLM_Agent.SelfEvolution.PolicyEvolution",
        "LLM_Agent.SelfEvolution.ModelParameterEvolution",
        "LLM_Agent.SelfEvolution.WorldModelEvolution",
        "LLM_Agent.SelfEvolution.EvolutionTrigger",
        "LLM_Agent.SelfEvolution.EvolutionFeedback",
        "LLM_Agent.SelfEvolution.CreditAssignment",
        "LLM_Agent.SelfEvolution.ContinualLifelongEvolution",
        "LLM_Agent.SelfEvolution.MetaEvolution",
    ],
    "LLM_Agent_Workflow_Long_Horizon": [
        "LLM_Agent.Workflow.TaskDecomposition",
        "LLM_Agent.Workflow.Planning",
        "LLM_Agent.Workflow.Representation",
        "LLM_Agent.Workflow.Construction",
        "LLM_Agent.Workflow.SearchOptimization",
        "LLM_Agent.Workflow.AdaptiveWorkflow",
        "LLM_Agent.Workflow.ExecutionControl",
        "LLM_Agent.LongHorizon.StateManagement",
        "LLM_Agent.LongHorizon.Adaptation",
        "LLM_Agent.LongHorizon.LearningProblems",
    ],
    "Multi_LLM_Agent_Memory_Tool_Skill": [
        "Multi_LLM_Agent.SharedMemory.MemoryScope",
        "Multi_LLM_Agent.SharedMemory.Architecture",
        "Multi_LLM_Agent.SharedMemory.SharingPolicy",
        "Multi_LLM_Agent.SharedMemory.CollectiveExperience",
        "Multi_LLM_Agent.SharedMemory.MemoryCoordination",
        "Multi_LLM_Agent.SkillTool.MultiAgentSkill",
        "Multi_LLM_Agent.SkillTool.MultiAgentTool",
        "Multi_LLM_Agent.SkillTool.SkillRoleCoupling",
    ],
    "Multi_LLM_Agent_Collaboration_Communication": [
        "Multi_LLM_Agent.Organization.TeamFormation",
        "Multi_LLM_Agent.Organization.RoleDesign",
        "Multi_LLM_Agent.Organization.DivisionOfLabor",
        "Multi_LLM_Agent.Organization.OrganizationalStructure",
        "Multi_LLM_Agent.Organization.HeterogeneousAgents",
        "Multi_LLM_Agent.Routing.TaskAllocation",
        "Multi_LLM_Agent.Routing.AgentRouting",
        "Multi_LLM_Agent.Routing.ModelRouting",
        "Multi_LLM_Agent.Routing.ParticipationControl",
        "Multi_LLM_Agent.Routing.ContextRouting",
        "Multi_LLM_Agent.Communication.Representation",
        "Multi_LLM_Agent.Communication.ContentSelection",
        "Multi_LLM_Agent.Communication.Compression",
        "Multi_LLM_Agent.Communication.Protocol",
        "Multi_LLM_Agent.Communication.Timing",
        "Multi_LLM_Agent.Communication.EmergentCommunication",
        "Multi_LLM_Agent.Topology.StaticTopology",
        "Multi_LLM_Agent.Topology.TopologyProperties",
        "Multi_LLM_Agent.Topology.DynamicTopology",
        "Multi_LLM_Agent.Topology.TopologyOptimization",
        "Multi_LLM_Agent.Topology.InformationPropagation",
        "Multi_LLM_Agent.CollectiveReasoning.DebateDeliberation",
        "Multi_LLM_Agent.CollectiveReasoning.CritiqueRefinement",
        "Multi_LLM_Agent.CollectiveReasoning.Aggregation",
        "Multi_LLM_Agent.CollectiveReasoning.DistributedReasoning",
        "Multi_LLM_Agent.CollectiveReasoning.AdaptiveReasoning",
    ],
    "Multi_LLM_Agent_Evolution": [
        "Multi_LLM_Agent.Evolution.IndividualAgentEvolution",
        "Multi_LLM_Agent.Evolution.RoleEvolution",
        "Multi_LLM_Agent.Evolution.TeamEvolution",
        "Multi_LLM_Agent.Evolution.TopologyEvolution",
        "Multi_LLM_Agent.Evolution.WorkflowEvolution",
        "Multi_LLM_Agent.Evolution.CommunicationEvolution",
        "Multi_LLM_Agent.Evolution.PopulationEvolution",
        "Multi_LLM_Agent.Evolution.OrganizationSelfOrganization",
        "Multi_LLM_Agent.ScalingEfficiency.TeamSizeScaling",
        "Multi_LLM_Agent.ScalingEfficiency.CollaborationScaling",
        "Multi_LLM_Agent.ScalingEfficiency.CostAwareMAS",
        "Multi_LLM_Agent.ScalingEfficiency.SparseCollaboration",
        "Multi_LLM_Agent.ScalingEfficiency.StoppingComputationAllocation",
    ],
}


def topic_options_for_category(category_name):
    return TOPICS_BY_CATEGORY.get(category_name, [])


def normalize_primary_topic(value, category_name):
    """Accept only a taxonomy topic valid for the current discovery category."""
    value = str(value or "").strip()
    allowed = topic_options_for_category(category_name)
    if value in allowed:
        return value
    # Models occasionally return a valid ID with underscores, spaces or case
    # differences.  Canonicalize those harmless presentation differences while
    # refusing an actually different taxonomy topic.
    normalized_value = re.sub(r"[^a-z0-9]", "", value.lower())
    for topic in allowed:
        if normalized_value == re.sub(r"[^a-z0-9]", "", topic.lower()):
            return topic
    return "Unclassified"
