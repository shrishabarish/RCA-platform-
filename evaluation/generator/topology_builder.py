from typing import Dict, List
from evaluation.schema import TopologyData, TopologyNode

def get_6_node_topology() -> TopologyData:
    nodes = [
        TopologyNode(id="EC2", type="compute", label="EC2 Compute Host", criticality=0.9),
        TopologyNode(id="DynamoDB", type="database", label="DynamoDB Database", criticality=0.95),
        TopologyNode(id="Auth", type="auth", label="Auth & IdP Service", criticality=0.85),
        TopologyNode(id="Worker", type="worker", label="Background Worker", criticality=0.7),
        TopologyNode(id="API", type="service", label="Backend API Gateway", criticality=0.85),
        TopologyNode(id="Application", type="frontend", label="Web Application UI", criticality=0.6),
    ]
    edges = [
        ["API", "EC2"],
        ["API", "DynamoDB"],
        ["API", "Auth"],
        ["Worker", "DynamoDB"],
        ["Worker", "EC2"],
        ["Application", "API"],
        ["Application", "Worker"]
    ]
    return TopologyData(nodes=nodes, edges=edges, topology_version="6-node-v1")


def get_12_node_topology() -> TopologyData:
    nodes = [
        TopologyNode(id="ComputeCluster", type="compute", label="EC2/K8s Compute Nodes", criticality=0.9),
        TopologyNode(id="PostgresPrimary", type="database", label="PostgreSQL Primary DB", criticality=0.95),
        TopologyNode(id="RedisCache", type="cache", label="Redis In-Memory Cache", criticality=0.8),
        TopologyNode(id="ObjectStore", type="storage", label="S3 Object Store", criticality=0.75),
        TopologyNode(id="AuthService", type="auth", label="OAuth2/OIDC Auth Service", criticality=0.9),
        TopologyNode(id="PaymentGateway", type="service", label="Payment Processing Service", criticality=0.95),
        TopologyNode(id="OrderService", type="service", label="Order Management Service", criticality=0.85),
        TopologyNode(id="CatalogService", type="service", label="Catalog & Product Service", criticality=0.75),
        TopologyNode(id="NotificationService", type="service", label="Push & Email Service", criticality=0.65),
        TopologyNode(id="WorkerCluster", type="worker", label="Async Job Worker Pool", criticality=0.7),
        TopologyNode(id="APIGateway", type="service", label="Public Ingress Gateway", criticality=0.9),
        TopologyNode(id="WebFrontend", type="frontend", label="React/Next.js Web Frontend", criticality=0.6),
    ]
    edges = [
        ["APIGateway", "AuthService"],
        ["APIGateway", "OrderService"],
        ["APIGateway", "CatalogService"],
        ["OrderService", "PostgresPrimary"],
        ["OrderService", "PaymentGateway"],
        ["OrderService", "WorkerCluster"],
        ["OrderService", "ComputeCluster"],
        ["CatalogService", "RedisCache"],
        ["CatalogService", "PostgresPrimary"],
        ["CatalogService", "ComputeCluster"],
        ["PaymentGateway", "PostgresPrimary"],
        ["PaymentGateway", "ComputeCluster"],
        ["WorkerCluster", "ObjectStore"],
        ["WorkerCluster", "PostgresPrimary"],
        ["WorkerCluster", "ComputeCluster"],
        ["NotificationService", "WorkerCluster"],
        ["WebFrontend", "APIGateway"],
        ["WebFrontend", "NotificationService"],
    ]
    return TopologyData(nodes=nodes, edges=edges, topology_version="12-node-v1")


def get_20_node_topology() -> TopologyData:
    nodes = [
        # Layer 0: Storage & Databases (Dependencies)
        TopologyNode(id="RdsPostgres", type="database", label="RDS Aurora PostgreSQL", criticality=0.95),
        TopologyNode(id="DynamoTable", type="database", label="DynamoDB Metadata Store", criticality=0.9),
        TopologyNode(id="S3Bucket", type="storage", label="S3 Artifact Store", criticality=0.75),
        TopologyNode(id="ElastiCacheRedis", type="cache", label="ElastiCache Cluster", criticality=0.8),
        TopologyNode(id="KafkaBroker", type="queue", label="Kafka Event Stream", criticality=0.9),
        
        # Layer 1: Compute Infrastructure
        TopologyNode(id="EksComputeA", type="compute", label="EKS Node Group A", criticality=0.9),
        TopologyNode(id="EksComputeB", type="compute", label="EKS Node Group B", criticality=0.9),
        TopologyNode(id="LambdaWorker", type="lambda", label="Serverless Lambda Consumer", criticality=0.7),

        # Layer 2: Core Domain Services
        TopologyNode(id="UserAuthService", type="auth", label="IAM & Session Service", criticality=0.95),
        TopologyNode(id="BillingService", type="service", label="Billing & Stripe Invoicing", criticality=0.9),
        TopologyNode(id="InventoryService", type="service", label="Warehouse Inventory", criticality=0.8),
        TopologyNode(id="ShippingService", type="service", label="Logistics & Dispatch", criticality=0.75),
        TopologyNode(id="SearchIndexService", type="service", label="OpenSearch Engine", criticality=0.8),
        TopologyNode(id="FraudDetectionService", type="service", label="ML Fraud Scoring", criticality=0.85),
        TopologyNode(id="EmailService", type="service", label="Transactional Mailer", criticality=0.6),
        TopologyNode(id="AnalyticsWorker", type="worker", label="Clickhouse Pipeline", criticality=0.65),

        # Layer 3: Edge & Gateways
        TopologyNode(id="PublicApiGateway", type="service", label="External API Router", criticality=0.9),
        TopologyNode(id="AdminApiGateway", type="service", label="Internal Admin Router", criticality=0.8),
        TopologyNode(id="MobileAppBackend", type="service", label="BFF Mobile Service", criticality=0.7),
        TopologyNode(id="WebPortal", type="frontend", label="Customer Portal SPA", criticality=0.65),
    ]
    edges = [
        # Edge to Domain
        ["WebPortal", "PublicApiGateway"],
        ["MobileAppBackend", "PublicApiGateway"],
        ["PublicApiGateway", "UserAuthService"],
        ["PublicApiGateway", "BillingService"],
        ["PublicApiGateway", "InventoryService"],
        ["PublicApiGateway", "SearchIndexService"],
        ["AdminApiGateway", "UserAuthService"],
        ["AdminApiGateway", "BillingService"],
        ["AdminApiGateway", "ShippingService"],
        
        # Domain to Domain
        ["BillingService", "FraudDetectionService"],
        ["BillingService", "EmailService"],
        ["InventoryService", "ShippingService"],
        ["ShippingService", "EmailService"],
        
        # Domain to Queue/Kafka
        ["BillingService", "KafkaBroker"],
        ["InventoryService", "KafkaBroker"],
        ["FraudDetectionService", "KafkaBroker"],
        ["AnalyticsWorker", "KafkaBroker"],
        ["LambdaWorker", "KafkaBroker"],
        
        # Domain to Storage / Database
        ["UserAuthService", "DynamoTable"],
        ["UserAuthService", "ElastiCacheRedis"],
        ["BillingService", "RdsPostgres"],
        ["InventoryService", "RdsPostgres"],
        ["ShippingService", "RdsPostgres"],
        ["SearchIndexService", "ElastiCacheRedis"],
        ["AnalyticsWorker", "S3Bucket"],
        ["LambdaWorker", "S3Bucket"],

        # Domain to Compute
        ["BillingService", "EksComputeA"],
        ["InventoryService", "EksComputeA"],
        ["SearchIndexService", "EksComputeB"],
        ["FraudDetectionService", "EksComputeB"],
        ["AnalyticsWorker", "EksComputeB"],
    ]
    return TopologyData(nodes=nodes, edges=edges, topology_version="20-node-v1")


def get_topology_by_size(size: int) -> TopologyData:
    if size == 6:
        return get_6_node_topology()
    elif size == 12:
        return get_12_node_topology()
    elif size == 20:
        return get_20_node_topology()
    else:
        raise ValueError(f"Unsupported topology size: {size}. Supported: 6, 12, 20")
