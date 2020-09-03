from io import TextIOWrapper
from json import load
from typing import List, NamedTuple, Optional


class LDAPConfigSchema(NamedTuple):
    url: str
    search_base: str
    bind_user: str
    bind_password: str
    admin_groups: List[str]
    use_tls: bool = True


class GoogleConfigSchema(NamedTuple):
    scopes: List[str]
    project_id: str
    client_id: str
    client_email: str
    private_key: str
    private_key_id: str
    token_uri: str
    type: str
    delegated_user: Optional[str]


class SlackConfigSchema(NamedTuple):
    api_token: str
    signing_secret: str
    app_id: str
    approvals_channel: str


class DatabaseConfigSchema(NamedTuple):
    host: str
    port: int
    user: str
    password: str
    dbname: str


class ConfigSchema(NamedTuple):
    ldap: LDAPConfigSchema
    google: GoogleConfigSchema
    slack: SlackConfigSchema
    database: DatabaseConfigSchema
    domain: str
    approval_timeout: int
    host: str
    port: int
    sockfile: str
    audit_tokens: List[str]

    @classmethod
    def from_json_file(cls, file_handle: TextIOWrapper) -> "ConfigSchema":
        config_data = load(file_handle)

        return cls(
            ldap=LDAPConfigSchema(**config_data["ldap"]),
            google=GoogleConfigSchema(**config_data["google"]),
            slack=SlackConfigSchema(**config_data["slack"]),
            database=DatabaseConfigSchema(**config_data["database"]),
            domain=config_data["domain"],
            host=config_data.get("host"),
            port=config_data.get("port"),
            sockfile=config_data.get("sockfile"),
            approval_timeout=config_data.get("approval_timeout", 30),
            audit_tokens=config_data.get("audit_tokens", []),
        )
