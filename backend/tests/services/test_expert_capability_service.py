from app.domain.models.expert_profile import ExpertProfile
from app.services.expert_capability_service import ExpertCapabilityService


def test_expert_capability_service_treats_comment_contract_signals_as_correctness_relevance():
    service = ExpertCapabilityService()
    expert = ExpertProfile(
        expert_id="correctness_business",
        name="Correctness",
        name_zh="正确性与业务专家",
        role="correctness",
        enabled=True,
        activation_hints=["service", "todo", "comment", "注释", "未实现"],
        required_checks=["注释、方法名和接口承诺的行为是否真正落地"],
        system_prompt="prompt",
    )

    score = service.score_hunk_relevance(
        expert,
        "src/main/java/com/example/OrderService.java",
        "+ // TODO: 创建订单后自动扣减库存\n+ return orderRepository.save(order);",
        "",
    )

    assert score > 0
