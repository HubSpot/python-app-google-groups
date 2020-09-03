from typing import Dict, List
from uuid import UUID


class LDAPUser(object):
    """
    Represents a user loaded from LDAP.
    Aliases is a map of service names to UIDs
    """

    def __init__(
        self,
        guid: str,
        email: str,
        location: str,
        name: str,
        dn: str,
        username: str,
        aliases: Dict[str, str],
        is_admin: bool = False,
    ) -> None:
        self.guid = guid
        self.email = email
        self.location = location
        self.name = name
        self.dn = dn
        self.username = username
        self.aliases = aliases
        self.is_admin = is_admin

    ldap_attribute_map: Dict[str, str] = {
        "guid": "objectGUID",
        "email": "mail",
        "location": "l",
        "name": "displayName",
        "dn": "distinguishedName",
        "username": "samaccountname",
        "aliases": "proxyAddresses",
    }

    @classmethod
    def from_ldap(cls, ldap_data: Dict[str, List[str]]) -> "LDAPUser":
        mapped_data = {
            key: ldap_data[ldap_key][0] if ldap_key in ldap_data else ""
            for key, ldap_key in cls.ldap_attribute_map.items()
        }

        # Map out the aliases to a dictionary
        aliases = {}
        for alias in ldap_data.get(cls.ldap_attribute_map["aliases"], []):
            split_alias = alias.split(":")
            if len(split_alias) >= 2:
                name, uid = split_alias[0].strip().lower(), ":".join(split_alias[1:]).strip()
                aliases[name] = uid
        mapped_data["aliases"] = aliases

        # Convert the objectGUID to a string instead of bytes
        guid = mapped_data["guid"]
        if type(guid) == int:
            mapped_data["guid"] = str(UUID(int=guid))
        elif type(guid) == bytes:
            mapped_data["guid"] = str(UUID(bytes=guid))

        return cls(**mapped_data)

    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "LDAPUser":
        return cls(**data)

    @classmethod
    def ldap_attributes(cls) -> List[str]:
        return list(cls.ldap_attribute_map.values())
