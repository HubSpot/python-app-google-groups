from typing import Dict, List, Optional


class GoogleGroupMember(object):
    table_name = "ggroups_cache_groups_members"

    def __init__(
        self,
        member_id: str,
        email: str,
        member_type: str,
        role: str,
        status: str,
        etag: str,
        delivery_settings: str,
    ) -> None:
        self.member_id: str = member_id
        self.email: str = email
        self.member_type: str = member_type
        self.role: str = role
        self.status: str = status
        self.etag: str = etag
        self.delivery_settings: str = delivery_settings

    @property
    def is_owner(self) -> bool:
        return self.role in ["MANAGER", "OWNER"]

    @classmethod
    def from_api(cls, raw_member: Dict[str, any]) -> "GoogleGroupMember":
        return cls(
            email=raw_member["email"],
            member_id=raw_member["id"],
            member_type=raw_member["type"],
            role=raw_member["role"],
            status=raw_member.get("status", "ACTIVE"),
            etag=raw_member["etag"],
            delivery_settings=raw_member.get("delivery_settings", "ALL_MAIL"),
        )

    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "GoogleGroupMember":
        return cls(**data)

    @classmethod
    def from_db(cls, row: Dict[str, any]) -> "GoogleGroupMember":
        if "group_id" in row:
            del row["group_id"]
        return cls.from_dict(row)

    def __eq__(self, other: any) -> bool:
        if isinstance(other, str):
            return other == self.email


class GoogleGroup(object):
    table_name = "ggroups_cache_groups"
    table_name_aliases = "ggroups_cache_groups_aliases"

    def __init__(
        self,
        group_id: str,
        name: str,
        email: str,
        description: str,
        etag: str,
        aliases: List[str],
        protected: bool = True,
    ) -> None:
        self.group_id = group_id
        self.name = name
        self.email = email
        self.description = description
        self.etag = etag
        self.aliases = aliases
        self.protected = protected
        self.members: List[GoogleGroupMember] = []

    @property
    def __dict__(self) -> Dict[str, any]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "email": self.email,
            "description": self.description,
            "etag": self.etag,
            "aliases": self.aliases,
            "protected": self.protected,
            "members": [vars(m) for m in self.members],
        }

    @classmethod
    def from_api(cls, raw_group: Dict[str, any]) -> "GoogleGroup":
        aliases = raw_group.get("aliases", []) + raw_group.get("nonEditableAliases", [])
        return cls(
            group_id=raw_group["id"],
            aliases=aliases,
            **{k: raw_group[k] for k in ["name", "email", "description", "etag"]},
        )

    @classmethod
    def from_dict(cls, data: Dict[str, any]) -> "GoogleGroup":
        members: List[GoogleGroupMember] = data.get("members", [])
        instance = cls(**{k: v for k, v in data.items() if k != "members"})
        for member in members:
            instance.add_member(GoogleGroupMember.from_dict(member))
        return instance

    @classmethod
    def from_db(cls, row: Dict[str, any]) -> "GoogleGroup":
        row.setdefault("aliases", [])
        row["protected"] = bool(int(row["protected"]))
        return cls.from_dict(row)

    @property
    def owners(self) -> List[GoogleGroupMember]:
        return [member for member in self.members if member.is_owner]

    def add_member(self, member: GoogleGroupMember) -> None:
        self.members.append(member)

    def remove_member(self, member: GoogleGroupMember) -> None:
        # Pass by reference \o/
        self.members.remove(member)

    def get_member_from_email(self, email: str) -> Optional[GoogleGroupMember]:
        for member in self.members:
            if member.email == email:
                return member

    def add_aliases(self, aliases: List[str]) -> None:
        self.aliases += aliases

    def __eq__(self, other: any) -> bool:
        if isinstance(other, str):
            return self.name == other or other in self.aliases

        if issubclass(other, self.__class__):
            return self.group_id == other.group_id

        return False

    def __contains__(self, other: any) -> bool:
        if isinstance(other, str):
            return other in (member.email for member in self.members)

        return False
