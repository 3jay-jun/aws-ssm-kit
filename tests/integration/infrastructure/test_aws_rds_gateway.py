import boto3
from botocore.stub import Stubber

from aws_connect.domain.aws_profile import PlainCredentials
from aws_connect.infrastructure.aws_rds_gateway import Boto3RdsEndpointGateway


def _credentials() -> PlainCredentials:
    return PlainCredentials("ACCESSKEYTEST0001", "not-sensitive-test-value", "fixture-token")


def test_rds_endpoint_catalog_paginates_and_sorts_identifiers() -> None:
    client = boto3.client(
        "rds",
        region_name="ap-northeast-2",
        aws_access_key_id="ACCESSKEYTEST0001",
        aws_secret_access_key="not-sensitive-test-value",  # pragma: allowlist secret
        aws_session_token="fixture-token",
    )
    gateway = Boto3RdsEndpointGateway(lambda _credentials, _region: client)
    stubber = Stubber(client)
    stubber.add_response(
        "describe_db_instances",
        {
            "DBInstances": [
                {
                    "DBInstanceIdentifier": "zeta",
                    "DBInstanceClass": "db.t3.micro",
                    "Engine": "mysql",
                    "DBInstanceStatus": "available",
                    "MasterUsername": "fixture",
                    "Endpoint": {"Address": "zeta.internal", "Port": 3306},
                    "AllocatedStorage": 20,
                    "PreferredBackupWindow": "00:00-00:30",
                    "BackupRetentionPeriod": 1,
                    "DBSecurityGroups": [],
                    "VpcSecurityGroups": [],
                    "DBParameterGroups": [],
                    "AvailabilityZone": "ap-northeast-2a",
                    "PreferredMaintenanceWindow": "sun:00:00-sun:00:30",
                    "PendingModifiedValues": {},
                    "MultiAZ": False,
                    "EngineVersion": "8.0",
                    "AutoMinorVersionUpgrade": True,
                    "ReadReplicaDBInstanceIdentifiers": [],
                    "LicenseModel": "general-public-license",
                    "OptionGroupMemberships": [],
                    "PubliclyAccessible": False,
                    "StorageType": "gp2",
                    "DbInstancePort": 0,
                    "StorageEncrypted": False,
                    "DbiResourceId": "db-EXAMPLE",
                    "CACertificateIdentifier": "rds-ca-rsa2048-g1",
                    "DomainMemberships": [],
                    "CopyTagsToSnapshot": False,
                    "MonitoringInterval": 0,
                    "DBInstanceArn": "arn:aws:rds:ap-northeast-2:123456789012:db:zeta",
                    "IAMDatabaseAuthenticationEnabled": False,
                    "PerformanceInsightsEnabled": False,
                    "DeletionProtection": False,
                    "AssociatedRoles": [],
                    "TagList": [],
                    "CustomerOwnedIpEnabled": False,
                    "ActivityStreamStatus": "stopped",
                    "BackupTarget": "region",
                    "NetworkType": "IPV4",
                    "StorageThroughput": 0,
                    "DedicatedLogVolume": False,
                    "EngineLifecycleSupport": "open-source-rds-extended-support-disabled",
                }
            ]
        },
        {},
    )

    with stubber:
        result = gateway.list_endpoints(_credentials(), "ap-northeast-2")

    assert [(item.identifier, item.host, item.port) for item in result] == [
        ("zeta", "zeta.internal", 3306)
    ]
