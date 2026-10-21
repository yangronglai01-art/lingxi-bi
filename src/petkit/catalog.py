"""Versioned metadata. Compact specs use name:type:Chinese description."""
from dataclasses import asdict, dataclass, field
from .settings import VERSION


@dataclass
class Table:
    name: str
    domain: str
    description: str
    grain: str
    columns: dict
    core: bool = True
    aliases: list = field(default_factory=list)
    source: str = "自建模拟数据"

    def payload(self):
        return asdict(self)


def table(name, domain, description, grain, spec):
    columns = {}
    types = {"s": "VARCHAR", "i": "BIGINT", "n": "DOUBLE", "d": "DATE", "t": "TIMESTAMP"}
    for item in spec.split("|"):
        key, typ, label = item.split(":", 2)
        columns[key] = {"type": types[typ], "description": label}
    return Table(name, domain, description, grain, columns)


def core_tables():
    specs = [
        ("dim_product","product","智能宠物硬件产品型号 主数据","每型号一行","product_id:s:型号编码|product_name:s:型号名称|category:s:类别 喂食器 饮水机 猫砂盆|unit_price:n:标准售价元"),
        ("dim_sku","product","商品SKU 包装规格","每SKU一行","sku_id:s:商品编码|product_id:s:型号编码|sku_name:s:商品名称"),
        ("dim_channel","sales","销售渠道 电商 自营 经销","每渠道一行","channel_id:s:渠道编码|channel_name:s:渠道名称"),
        ("dim_region","sales","销售区域","每区域一行","region_id:s:区域编码|region_name:s:区域名称"),
        ("dim_customer","customer","虚拟客户 不含真实个人信息","每客户一行","customer_id:s:客户编码|customer_name:s:虚拟姓名|region_id:s:区域编码"),
        ("dim_device","device","实物设备 序列号及型号","每台设备一行","device_id:s:设备编码|product_id:s:型号编码|manufactured_date:d:生产日期"),
        ("dim_firmware","device","固件版本","每版本一行","firmware_id:s:版本编码|firmware_name:s:固件版本|release_date:d:发布日期"),
        ("dim_fault","service","售后故障分类","每故障类别一行","fault_id:s:故障编码|fault_name:s:故障名称"),
        ("dim_warehouse","supply","仓库","每仓库一行","warehouse_id:s:仓库编码|warehouse_name:s:仓库名称|region_id:s:区域编码"),
        ("dim_supplier","supply","模拟供应商","每供应商一行","supplier_id:s:供应商编码|supplier_name:s:供应商名称"),
        ("dim_calendar","shared","日期维度","每日一行","date_id:d:日期|month:s:月份|quarter:i:季度"),
        ("dim_employee","service","模拟客服员工","每员工一行","employee_id:s:员工编码|employee_name:s:虚拟姓名"),
        ("fact_order","sales","已支付订单头 订单金额不要与明细重复累计","每订单一行","order_id:s:订单编码|customer_id:s:客户编码|channel_id:s:渠道编码|region_id:s:区域编码|paid_date:d:支付日期|paid_amount:n:实付金额元|status:s:订单状态"),
        ("fact_order_item","sales","订单行 含SKU和数量","每订单行一行","item_id:s:明细编码|order_id:s:订单编码|sku_id:s:商品编码|quantity:i:设备数量|amount:n:明细实付金额|cost:n:明细成本"),
        ("fact_payment","finance","支付流水","每支付一行","payment_id:s:支付编码|order_id:s:订单编码|paid_date:d:支付日期|amount:n:支付金额"),
        ("fact_refund","sales","退款 按订单行归属原支付月","每退款一行","refund_id:s:退款编码|item_id:s:订单行编码|refund_date:d:退款日期|amount:n:退款金额"),
        ("fact_shipment","supply","已发货物流记录","每发货单一行","shipment_id:s:发货单编码|order_id:s:订单编码|warehouse_id:s:仓库编码|ship_date:d:发货日期"),
        ("bridge_order_device","sales","订单行与实际设备桥接 每台售出设备只对应一行","每售出设备一行","device_id:s:设备编码|item_id:s:订单行编码"),
        ("fact_device_activation","device","首次激活 售出后30天激活率分子来源","每激活设备一行","device_id:s:设备编码|activation_date:d:首次激活日期"),
        ("fact_device_binding","device","设备绑定关系","每首次绑定一行","device_id:s:设备编码|customer_id:s:绑定客户编码|bound_date:d:绑定日期"),
        ("fact_device_day","device","设备每日活跃在线统计 仅采样观察窗口","每设备每日一行","device_id:s:设备编码|event_date:d:事件日期|firmware_id:s:固件版本|online:i:当日在线标识|active:i:当日活跃标识|event_count:i:事件次数"),
        ("fact_device_event","device","设备使用事件 不包含视频","每事件一行","event_id:s:事件编码|device_id:s:设备编码|event_date:d:事件日期|event_type:s:喂食 饮水 如厕"),
        ("fact_firmware_upgrade","device","固件升级记录","每升级一行","upgrade_id:s:升级编码|device_id:s:设备编码|firmware_id:s:目标固件|upgrade_date:d:升级日期|success:i:成功标识"),
        ("fact_service_ticket","service","售后工单 一台设备可以多次报障","每工单一行","ticket_id:s:工单编码|device_id:s:设备编码|fault_id:s:故障编码|employee_id:s:客服编码|created_date:d:创建日期|status:s:工单状态"),
        ("fact_ticket_status","service","工单状态记录","每状态事件一行","status_id:s:状态编码|ticket_id:s:工单编码|event_date:d:日期|status:s:状态"),
        ("fact_repair","service","维修记录","每维修一行","repair_id:s:维修编码|ticket_id:s:工单编码|repair_date:d:维修日期|cost:n:维修成本"),
        ("fact_replacement","service","换新关系 新机仅属于换新设备集合","每换新一行","replacement_id:s:换新编码|ticket_id:s:工单编码|old_device_id:s:原设备|new_device_id:s:新设备|replace_date:d:换新日期"),
        ("fact_feedback","service","售后反馈评分","每工单评价一行","feedback_id:s:评价编码|ticket_id:s:工单编码|score:i:满意度1到5|feedback_date:d:评价日期"),
        ("fact_inventory_day","supply","每日库存快照","每商品仓库每日一行","stock_date:d:快照日期|sku_id:s:商品编码|warehouse_id:s:仓库编码|quantity:i:结存数量"),
        ("fact_stock_movement","supply","仓库出入库流水","每流水一行","movement_id:s:流水编码|sku_id:s:商品编码|warehouse_id:s:仓库编码|movement_date:d:发生日期|quantity:i:变动数量 正入负出"),
        ("fact_purchase_order","supply","采购订单","每采购订单一行","purchase_id:s:采购编码|supplier_id:s:供应商编码|purchase_date:d:采购日期"),
        ("fact_purchase_item","supply","采购明细","每采购行一行","purchase_item_id:s:采购行编码|purchase_id:s:采购编码|sku_id:s:商品编码|quantity:i:采购数量|amount:n:采购金额"),
        ("fact_receipt","supply","采购收货","每收货一行","receipt_id:s:收货编码|purchase_item_id:s:采购行编码|warehouse_id:s:仓库编码|receipt_date:d:收货日期|quantity:i:收货数量"),
        ("fact_campaign","marketing","商品营销活动","每活动一行","campaign_id:s:活动编码|product_id:s:型号编码|start_date:d:开始日期|end_date:d:结束日期|budget:n:活动预算"),
        ("fact_campaign_day","marketing","营销活动日报","每活动每日一行","campaign_id:s:活动编码|event_date:d:日期|impressions:i:曝光|clicks:i:点击|spend:n:花费"),
        ("fact_subscription","customer","模拟服务订阅 不代表真实产品收费方案","每订阅一行","subscription_id:s:订阅编码|customer_id:s:客户编码|start_date:d:开始日期|end_date:d:结束日期|amount:n:订阅金额"),
        ("fact_app_session","customer","虚拟App会话","每会话一行","session_id:s:会话编码|customer_id:s:客户编码|event_date:d:日期|duration_seconds:i:时长秒"),
        ("fact_support_contact","service","客服沟通次数","每联系一行","contact_id:s:沟通编码|ticket_id:s:工单编码|employee_id:s:员工编码|contact_date:d:日期"),
        ("fact_product_price","product","商品价格历史","每SKU生效日一行","sku_id:s:商品编码|effective_date:d:生效日期|price:n:售价"),
        ("fact_product_cost","finance","商品成本历史","每SKU生效日一行","sku_id:s:商品编码|effective_date:d:生效日期|cost:n:成本"),
    ]
    return [table(*s) for s in specs]


SUBJECTS = {
 "sales": ("销售", "order:订单 payment:支付 refund:退款 shipment:发货 channel:渠道 promotion:促销 coupon:优惠券 cart:购物车 conversion:转化 basket:客单 repeat:复购 region:区域 sku:商品 model:型号 bundle:套装 dealer:经销 retail:零售 online:线上 wholesale:批发 return:退货 discount:折扣 fulfillment:履约 settlement:结算"),
 "device": ("设备", "activation:激活 binding:绑定 online:在线 offline:离线 active:活跃 feeding:喂食 drinking:饮水 litter:如厕 firmware:固件 upgrade:升级 reboot:重启 battery:电量 sensor:传感器 motor:电机 connectivity:连接 error:异常 usage:使用 cleaning:清洁 reminder:提醒 pairing:配网 maintenance:养护 stability:稳定性 retention:留存"),
 "service": ("售后", "ticket:工单 fault:故障 repair:维修 replacement:换新 satisfaction:满意度 response:响应 resolution:解决 escalation:升级处理 repeat:重复报修 warranty:保修 return:退机 contact:沟通 parts:备件 engineer:工程师 backlog:积压 complaint:投诉 closure:结案 inspection:检测 logistics:寄修 callback:回访 diagnosis:诊断 severity:严重度 compensation:补偿"),
 "supply": ("供应链", "inventory:库存 purchase:采购 receipt:收货 outbound:出库 inbound:入库 transfer:调拨 supplier:供应商 warehouse:仓库 stockout:缺货 overstock:积压 turnover:周转 leadtime:交期 delivery:交付 allocation:分配 planning:计划 replenishment:补货 safety:安全库存 batch:批次 material:物料 packaging:包装 freight:运费 damage:破损 fulfillment:履约"),
 "marketing": ("营销", "campaign:活动 impressions:曝光 clicks:点击 conversion:转化 spend:花费 audience:人群 acquisition:获客 referral:推荐 search:搜索 content:内容 social:社交 livestream:直播 affiliate:联盟 coupon:优惠 landing:落地页 funnel:漏斗 attribution:归因 retention:留存 repurchase:复购 reach:触达 creative:素材 experiment:实验 frequency:频次"),
 "finance": ("财务", "revenue:收入 cost:成本 margin:毛利 expense:费用 budget:预算 invoice:开票 payment:付款 collection:收款 refund:退款 settlement:结算 receivable:应收 payable:应付 cash:现金 tax:税费 discount:折让 freight:运费 commission:佣金 variance:差异 accrual:计提 reconciliation:对账 allocation:分摊 depreciation:折旧 forecast:预测"),
 "customer": ("客户", "registration:注册 login:登录 session:会话 active:活跃 retention:留存 churn:流失 membership:会员 subscription:订阅 preference:偏好 segment:分群 lifecycle:生命周期 engagement:互动 referral:推荐 satisfaction:满意度 contact:联系 household:家庭 pet:宠物 profile:画像 notification:通知 consent:同意 deletion:删除 request:请求 journey:旅程"),
 "product": ("商品", "model:型号 sku:商品 category:类目 series:系列 price:价格 cost:成本 listing:上架 launch:发布 lifecycle:生命周期 bundle:套装 accessory:配件 consumable:耗材 compatibility:兼容 feature:功能 specification:规格 review:评价 rating:评分 defect:缺陷 certification:认证 packaging:包装 localization:本地化 retirement:退市 comparison:比较"),
 "quality": ("质量", "inspection:检验 incoming:来料 process:制程 outgoing:出厂 defect:缺陷 yield:良率 batch:批次 supplier:供方 sampling:抽检 reliability:可靠性 aging:老化 stress:应力 safety:安全 compliance:合规 calibration:校准 rework:返工 scrap:报废 complaint:投诉 traceability:追溯 corrective:纠正 preventive:预防 audit:审核 failure:失效"),
 "operations": ("经营", "overview:总览 region:区域 channel:渠道 model:型号 daily:日报 weekly:周报 monthly:月报 target:目标 achievement:达成 growth:增长 seasonality:季节性 forecast:预测 exception:异常 efficiency:效率 productivity:生产率 resource:资源 capacity:产能 demand:需求 planning:计划 collaboration:协作 health:健康度 milestone:里程碑 risk:风险"),
}


def all_tables():
    tables = core_tables()
    for domain, (label, subjects) in SUBJECTS.items():
        for subject in subjects.split():
            code, zh = subject.split(":")
            for period, grain in [("daily", "每日"), ("monthly", "每月")]:
                t = table(f"agg_{domain}_{code}_{period}", domain,
                    f"{label}域{zh}{grain}模拟分析快照；独立合成量值，不能代替核心事实指标",
                    f"{grain}每型号一行",
                    "period_date:d:统计期起始日|product_id:s:型号编码|record_count:i:该主题模拟记录数|amount:n:该主题模拟量值|source_version:s:数据版本")
                t.core = False
                t.aliases = [zh, label, "扩展分析"]
                tables.append(t)
    assert len(tables) == 500, "目录必须恰好500表"
    assert len({t.name for t in tables}) == 500
    return {t.name: t.payload() for t in tables}


RELATIONS = []
def rel(left, key, right, target, cardinality="many_to_one"):
    RELATIONS.append(dict(left=left, left_key=key, right=right, right_key=target,
                          cardinality=cardinality, approved=True))

for args in [
 ("dim_sku","product_id","dim_product","product_id"),
 ("dim_customer","region_id","dim_region","region_id"),
 ("dim_device","product_id","dim_product","product_id"),
 ("dim_warehouse","region_id","dim_region","region_id"),
 ("fact_order","customer_id","dim_customer","customer_id"),
 ("fact_order","channel_id","dim_channel","channel_id"),
 ("fact_order","region_id","dim_region","region_id"),
 ("fact_order_item","order_id","fact_order","order_id"),
 ("fact_order_item","sku_id","dim_sku","sku_id"),
 ("fact_payment","order_id","fact_order","order_id"),
 ("fact_refund","item_id","fact_order_item","item_id"),
 ("fact_shipment","order_id","fact_order","order_id"),
 ("fact_shipment","warehouse_id","dim_warehouse","warehouse_id"),
 ("bridge_order_device","item_id","fact_order_item","item_id"),
 ("bridge_order_device","device_id","dim_device","device_id"),
 ("fact_device_activation","device_id","dim_device","device_id"),
 ("fact_device_binding","device_id","dim_device","device_id"),
 ("fact_device_binding","customer_id","dim_customer","customer_id"),
 ("fact_device_day","device_id","dim_device","device_id"),
 ("fact_device_day","firmware_id","dim_firmware","firmware_id"),
 ("fact_device_event","device_id","dim_device","device_id"),
 ("fact_firmware_upgrade","device_id","dim_device","device_id"),
 ("fact_firmware_upgrade","firmware_id","dim_firmware","firmware_id"),
 ("fact_service_ticket","device_id","dim_device","device_id"),
 ("fact_service_ticket","fault_id","dim_fault","fault_id"),
 ("fact_service_ticket","employee_id","dim_employee","employee_id"),
 ("fact_ticket_status","ticket_id","fact_service_ticket","ticket_id"),
 ("fact_repair","ticket_id","fact_service_ticket","ticket_id"),
 ("fact_replacement","ticket_id","fact_service_ticket","ticket_id"),
 ("fact_replacement","old_device_id","dim_device","device_id"),
 ("fact_replacement","new_device_id","dim_device","device_id"),
 ("fact_feedback","ticket_id","fact_service_ticket","ticket_id"),
 ("fact_inventory_day","sku_id","dim_sku","sku_id"),
 ("fact_inventory_day","warehouse_id","dim_warehouse","warehouse_id"),
 ("fact_stock_movement","sku_id","dim_sku","sku_id"),
 ("fact_stock_movement","warehouse_id","dim_warehouse","warehouse_id"),
 ("fact_purchase_order","supplier_id","dim_supplier","supplier_id"),
 ("fact_purchase_item","purchase_id","fact_purchase_order","purchase_id"),
 ("fact_purchase_item","sku_id","dim_sku","sku_id"),
 ("fact_receipt","purchase_item_id","fact_purchase_item","purchase_item_id"),
 ("fact_receipt","warehouse_id","dim_warehouse","warehouse_id"),
 ("fact_campaign","product_id","dim_product","product_id"),
 ("fact_campaign_day","campaign_id","fact_campaign","campaign_id"),
 ("fact_subscription","customer_id","dim_customer","customer_id"),
 ("fact_app_session","customer_id","dim_customer","customer_id"),
 ("fact_support_contact","ticket_id","fact_service_ticket","ticket_id"),
 ("fact_support_contact","employee_id","dim_employee","employee_id"),
 ("fact_product_price","sku_id","dim_sku","sku_id"),
 ("fact_product_cost","sku_id","dim_sku","sku_id"),
]: rel(*args)

# Explicit device-key joins, equivalent to the approved device hub path.
rel("fact_device_activation","device_id","bridge_order_device","device_id","one_to_one")
rel("fact_service_ticket","device_id","bridge_order_device","device_id","many_to_one")

METRICS = [
 dict(id="net_sales", name="净销售额", aliases=["销售额","收入","销售趋势"],
      definition="订单行实付减截至数据截止日该行退款；退款归属原订单支付日；元人民币，不做税额拆分",
      dependencies=["fact_order","fact_order_item","fact_refund","dim_sku","dim_product","dim_channel"],
      grain="订单行先汇总退款再关联；按支付日统计", version=VERSION),
 dict(id="activation30", name="售出30天激活率", aliases=["激活率","激活"],
      definition="截至2026-10-16观察满30天的售出设备，支付日起0至30天激活的设备数/该批设备数；含后续退款设备",
      dependencies=["fact_order","fact_order_item","bridge_order_device","dim_device","dim_product","fact_device_activation"],
      grain="设备去重；排除未成熟观察窗", version=VERSION),
 dict(id="service30", name="售出30天售后设备率", aliases=["售后率","故障率","售后设备占比"],
      definition="观察满30天售出设备，支付日起0至30天有工单的设备去重数/售出设备去重数；包含退款设备；不是工单数占比",
      dependencies=["fact_order","fact_order_item","bridge_order_device","dim_device","dim_product","fact_service_ticket"],
      grain="设备去重；工单先按设备归并", version=VERSION),
 dict(id="offline_rate", name="设备离线率", aliases=["离线率","离线","在线率"],
      definition="观测设备日中online=0的记录数/总设备日记录数；仅覆盖采样观察日，不代表全量设备实时状态",
      dependencies=["fact_device_day","dim_device","dim_product","dim_firmware"],grain="设备日",version=VERSION),
 dict(id="tickets", name="工单数量", aliases=["工单数","工单","故障分类"],
      definition="按工单创建日统计工单数，不等同于售后设备数",
      dependencies=["fact_service_ticket","dim_fault","dim_device","dim_product"],grain="工单",version=VERSION),
 dict(id="received_units", name="采购收货数量", aliases=["收货量","收货数量","收货件数","入库收货量"],
      definition="按收货日期汇总fact_receipt.quantity已接收数量；按仓库分组；不是采购订购数量、收货单数、库存余额或金额；包含历史种子及新增收货",
      dependencies=["fact_receipt","dim_warehouse"],grain="采购行每次收货事实；按收货日统计",version=VERSION),
 dict(id="inventory", name="期末库存", aliases=["库存","结存"],
      definition="每个SKU和仓库分别选取范围内最新快照，再按型号汇总；不跨日期累计库存快照",
      dependencies=["fact_inventory_day","dim_sku","dim_product","dim_warehouse"],grain="商品仓库快照",version=VERSION),
]


def catalog_payload():
    tables = all_tables()
    relationships = list(RELATIONS)
    for t in tables.values():
        if not t["core"]:
            relationships.append(dict(left=t["name"],left_key="product_id",right="dim_product",
                                      right_key="product_id",cardinality="many_to_one",approved=True))
    return dict(version=VERSION, synthetic=True, tables=tables, metrics=METRICS, relationships=relationships)
