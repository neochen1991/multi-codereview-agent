# 多Agent代码检视系统质量提升实施方案

## 文档信息
- 编制日期：2026-03-31
- 适用范围：Java业务代码检视
- 目标：系统性提升代码检视的准确性、覆盖率和可用性

---

## 一、置信度模型优化方案

### 1.1 当前模型问题诊断

```python
# 现有置信度计算模型（backend/app/services/orchestrator/nodes/detect_conflicts.py:49-54）
FINDING_TYPE_WEIGHTS = {
    "direct_defect": 1.0,      # 直接缺陷
    "test_gap": 0.8,           # 测试覆盖缺口
    "risk_hypothesis": 0.65,   # 风险假设
    "design_concern": 0.55,    # 设计关注
}
```

**现存问题：**
1. 权重固定，未考虑Java业务代码特性
2. 缺乏Spring框架特定缺陷类型的权重调整
3. 未区分Controller/Service/Repository层级的差异
4. 缺少对MyBatis Mapper XML问题的特殊权重

### 1.2 Java业务代码专项权重优化

```python
# 建议新增：Java业务代码专项权重配置
JAVA_BUSINESS_FINDING_TYPE_WEIGHTS = {
    # 数据一致性缺陷（最高优先级）
    "transaction_boundary_violation": 1.15,  # 事务边界违规（如Service方法内调用@Transactional方法）
    "concurrent_modification_risk": 1.12,   # 并发修改风险（无锁或锁粒度不当）
    "distributed_data_inconsistency": 1.10,  # 分布式数据不一致（TCC/Saga补偿缺失）
    
    # 直接缺陷（原有权重优化）
    "direct_defect": 1.0,
    "direct_defect_data_access": 1.05,      # 数据访问层直接缺陷（SQL注入风险）
    "direct_defect_api_contract": 1.03,     # API契约违规（Response字段缺失/类型不符）
    
    # MyBatis Mapper专项
    "mybatis_mapper_sql_risk": 0.95,        # Mapper XML SQL风险（动态SQL拼接）
    "mybatis_result_map_mismatch": 0.88,    # ResultMap与实体字段不匹配
    "mybatis_n_plus_one_risk": 0.85,        # N+1查询风险（未配置fetchType）
    
    # Spring框架专项
    "spring_bean_scope_mismatch": 0.82,     # Bean作用域不匹配（单例依赖原型）
    "spring_async_transaction_risk": 0.80,  # @Async与@Transactional共用风险
    "spring_event_ordering_risk": 0.75,     # 事件监听顺序风险
    
    # 测试覆盖（保持不变）
    "test_gap": 0.8,
    "test_gap_controller": 0.78,            # Controller层测试缺失
    "test_gap_service": 0.82,               # Service层测试缺失（更关键）
    
    # 风险假设（保持不变）
    "risk_hypothesis": 0.65,
    "risk_hypothesis_concurrency": 0.70,      # 并发相关风险假设权重略高
    
    # 设计关注（保持不变）
    "design_concern": 0.55,
    "design_concern_ddd_violation": 0.60,   # DDD分层违规略高
}
```

### 1.3 分层权重计算实现

```python
# 建议新增文件：backend/app/services/java_review_confidence_scorer.py

from typing import List, Dict, Any
from dataclasses import dataclass

@dataclass
class JavaFindingContext:
    """Java业务代码Finding的上下文信息"""
    file_path: str                          # 文件路径
    class_type: str                         # 类类型（Controller/Service/Repository/Mapper等）
    method_signature: str                   # 方法签名
    has_transactional: bool                 # 是否有@Transactional
    mybatis_mapper_xml: bool                # 是否为MyBatis Mapper XML
    spring_async: bool                      # 是否使用@Async
    spring_event_listener: bool             # 是否为事件监听器


class JavaReviewConfidenceScorer:
    """Java业务代码专用置信度评分器"""
    
    # 文件路径模式识别
    CONTROLLER_PATTERNS = ["**/controller/**", "**/web/**", "**/api/**", "**/rest/**"]
    SERVICE_PATTERNS = ["**/service/**", "**/domain/**", "**/application/**"]
    REPOSITORY_PATTERNS = ["**/repository/**", "**/dao/**", "**/mapper/**"]
    ENTITY_PATTERNS = ["**/entity/**", "**/po/**", "**/do/**"]
    DTO_PATTERNS = ["**/dto/**", "**/vo/**", "**/bo/**"]
    
    def __init__(self):
        self.base_weights = {
            "direct_defect": 1.0,
            "test_gap": 0.8,
            "risk_hypothesis": 0.65,
            "design_concern": 0.55,
        }
    
    def calculate_confidence(
        self,
        findings: List[Dict[str, Any]],
        context: JavaFindingContext
    ) -> Dict[str, Any]:
        """
        计算Java业务代码专项置信度
        
        核心逻辑：
        1. 根据文件类型识别分层（Controller/Service/Repository）
        2. 根据注解识别特性（@Transactional/@Async等）
        3. 根据finding类型应用专项权重
        4. 计算加权置信度并返回分解详情
        """
        
        # 步骤1：识别分层和特性
        layer = self._detect_layer(context.file_path)
        characteristics = self._detect_characteristics(context)
        
        # 步骤2：应用专项权重调整
        adjusted_weights = self._calculate_adjusted_weights(
            layer, characteristics, findings
        )
        
        # 步骤3：计算加权置信度
        weighted_confidence = self._calculate_weighted_confidence(
            findings, adjusted_weights
        )
        
        # 步骤4：计算奖励/惩罚
        consensus_bonus = self._calculate_consensus_bonus(findings)
        evidence_bonus = self._calculate_evidence_bonus(findings, layer)
        hypothesis_penalty = self._calculate_hypothesis_penalty(
            findings, layer, characteristics
        )
        
        # 步骤5：最终置信度
        final_confidence = self._apply_bounds(
            weighted_confidence + consensus_bonus + evidence_bonus - hypothesis_penalty
        )
        
        return {
            "confidence": final_confidence,
            "confidence_breakdown": {
                "base_weighted_confidence": weighted_confidence,
                "consensus_bonus": consensus_bonus,
                "evidence_bonus": evidence_bonus,
                "hypothesis_penalty": hypothesis_penalty,
                "final_confidence": final_confidence,
            },
            "context_analysis": {
                "layer": layer,
                "characteristics": characteristics,
                "adjusted_weights": adjusted_weights,
            },
            "findings_analysis": {
                "total_findings": len(findings),
                "participant_count": len(set(f.get("expert_id") for f in findings)),
                "has_direct_defect": any(f.get("finding_type") == "direct_defect" for f in findings),
            }
        }
    
    def _detect_layer(self, file_path: str) -> str:
        """识别代码分层"""
        for pattern in self.CONTROLLER_PATTERNS:
            if self._match_pattern(file_path, pattern):
                return "controller"
        for pattern in self.SERVICE_PATTERNS:
            if self._match_pattern(file_path, pattern):
                return "service"
        for pattern in self.REPOSITORY_PATTERNS:
            if self._match_pattern(file_path, pattern):
                return "repository"
        for pattern in self.ENTITY_PATTERNS:
            if self._match_pattern(file_path, pattern):
                return "entity"
        for pattern in self.DTO_PATTERNS:
            if self._match_pattern(file_path, pattern):
                return "dto"
        return "unknown"
    
    def _detect_characteristics(self, context: JavaFindingContext) -> Dict[str, bool]:
        """识别代码特性"""
        return {
            "transactional": context.has_transactional,
            "async": context.spring_async,
            "event_listener": context.spring_event_listener,
            "mybatis_mapper": context.mybatis_mapper_xml,
        }
    
    def _calculate_adjusted_weights(
        self,
        layer: str,
        characteristics: Dict[str, bool],
        findings: List[Dict[str, Any]]
    ) -> Dict[str, float]:
        """计算调整后的权重"""
        adjusted = dict(self.base_weights)
        
        # 根据分层调整权重
        if layer == "service":
            # Service层更关注事务和一致性
            if characteristics.get("transactional"):
                adjusted["direct_defect"] = 1.05
        
        elif layer == "repository":
            # Repository层更关注数据访问
            if characteristics.get("mybatis_mapper"):
                adjusted["risk_hypothesis"] = 0.70
        
        # 根据finding类型进一步调整
        for finding in findings:
            finding_type = finding.get("finding_type", "")
            
            # 事务边界违规权重提升
            if finding.get("subtype") == "transaction_boundary_violation":
                adjusted["direct_defect"] = max(adjusted["direct_defect"], 1.15)
            
            # MyBatis N+1风险权重提升
            if finding.get("subtype") == "mybatis_n_plus_one_risk":
                adjusted["risk_hypothesis"] = max(adjusted["risk_hypothesis"], 0.85)
        
        return adjusted
    
    def _calculate_weighted_confidence(
        self,
        findings: List[Dict[str, Any]],
        adjusted_weights: Dict[str, float]
    ) -> float:
        """计算加权置信度"""
        weighted_sum = 0.0
        total_weight = 0.0
        
        for finding in findings:
            finding_type = finding.get("finding_type", "risk_hypothesis")
            weight = adjusted_weights.get(finding_type, 0.65)
            confidence = float(finding.get("confidence", 0.0))
            
            weighted_sum += confidence * weight
            total_weight += weight
        
        return round(weighted_sum / max(total_weight, 1e-6), 2)
    
    def _calculate_consensus_bonus(self, findings: List[Dict[str, Any]]) -> float:
        """计算多专家一致性奖励"""
        participant_ids = {f.get("expert_id") for f in findings if f.get("expert_id")}
        participant_count = len(participant_ids)
        
        if participant_count <= 1:
            return 0.0
        
        return min(0.08, round(0.03 + 0.02 * (participant_count - 2), 2))
    
    def _calculate_evidence_bonus(
        self,
        findings: List[Dict[str, Any]],
        layer: str
    ) -> float:
        """计算证据链奖励"""
        # 收集证据信号
        evidence_signals = set()
        direct_evidence = False
        
        for finding in findings:
            # 检查证据字段
            for key in ["evidence", "cross_file_evidence", "context_files", "matched_rules", "violated_guidelines"]:
                for value in finding.get(key, []):
                    if value:
                        evidence_signals.add(str(value).strip())
            
            # 检查是否有直接证据
            if finding.get("finding_type") == "direct_defect":
                direct_evidence = True
        
        # 分层证据奖励加成
        layer_bonus = 0.0
        if layer == "service" and any("transaction" in s for s in evidence_signals):
            layer_bonus = 0.01
        elif layer == "repository" and any("sql" in s or "mapper" in s for s in evidence_signals):
            layer_bonus = 0.01
        
        base_bonus = min(0.06, round(min(len(evidence_signals), 4) * 0.01 + (0.02 if direct_evidence else 0.0), 2))
        return min(0.08, base_bonus + layer_bonus)
    
    def _calculate_hypothesis_penalty(
        self,
        findings: List[Dict[str, Any]],
        layer: str,
        characteristics: Dict[str, bool]
    ) -> float:
        """计算纯假设惩罚"""
        if not findings:
            return 0.0
        
        # 检查是否全部为risk_hypothesis
        all_hypothesis = all(f.get("finding_type") == "risk_hypothesis" for f in findings)
        if not all_hypothesis:
            return 0.0
        
        # 检查是否都需要验证
        all_need_verification = all(f.get("verification_needed", True) for f in findings)
        if not all_need_verification:
            return 0.0
        
        # 检查是否有直接证据
        has_direct_evidence = any(f.get("finding_type") == "direct_defect" for f in findings)
        if has_direct_evidence:
            return 0.0
        
        # 计算基础惩罚
        penalty = 0.05
        
        # 单专家惩罚
        participant_count = len(set(f.get("expert_id") for f in findings if f.get("expert_id")))
        if participant_count <= 1:
            penalty += 0.03
        
        # 证据不足惩罚
        evidence_count = sum(len(f.get("evidence", [])) for f in findings)
        if evidence_count <= 3:
            penalty += 0.02
        
        # 分层特性惩罚加成
        if layer == "service" and characteristics.get("transactional"):
            # Service层事务相关假设惩罚更高
            penalty = min(0.15, penalty + 0.02)
        elif layer == "repository":
            # Repository层数据访问假设
            penalty = min(0.14, penalty + 0.01)
        
        return min(0.18, round(penalty, 2))
    
    def _apply_bounds(self, confidence: float) -> float:
        """应用置信度边界"""
        return round(min(0.99, max(0.01, confidence)), 2)
    
    def _match_pattern(self, file_path: str, pattern: str) -> bool:
        """匹配文件路径模式"""
        import fnmatch
        return fnmatch.fnmatch(file_path.lower(), pattern.lower())


# 使用示例
if __name__ == "__main__":
    scorer = JavaReviewConfidenceScorer()
    
    # 构建Java代码上下文
    context = JavaFindingContext(
        file_path="com/example/order/service/OrderService.java",
        class_type="OrderService",
        method_signature="createOrder(OrderDTO)",
        has_transactional=True,
        mybatis_mapper_xml=False,
        spring_async=False,
        spring_event_listener=False,
    )
    
    # 示例findings
    findings = [
        {
            "finding_id": "f001",
            "expert_id": "correctness_business",
            "finding_type": "direct_defect",
            "confidence": 0.85,
            "subtype": "transaction_boundary_violation",
            "evidence": ["OrderService.createOrder调用自身@Transactional方法", "可能导致事务不生效"],
        },
        {
            "finding_id": "f002",
            "expert_id": "performance_reliability",
            "finding_type": "risk_hypothesis",
            "confidence": 0.70,
            "subtype": "mybatis_n_plus_one_risk",
            "evidence": ["查询订单列表时循环调用getOrderDetail", "未配置fetchType=lazy"],
        },
    ]
    
    # 计算置信度
    result = scorer.calculate_confidence(findings, context)
    
    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))
