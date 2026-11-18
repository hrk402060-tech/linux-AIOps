from typing import Annotated, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

PolicyId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
AccountId = Annotated[str, StringConstraints(min_length=1, max_length=36, pattern=r"^[a-zA-Z0-9_-]+$")]
PolicyName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)]
Permission = Annotated[str, StringConstraints(max_length=72, pattern=r"^[a-z0-9-]+:(read|write)$")]
_Value = TypeVar("_Value", bound=str)


def _require_unique(values: list[_Value]) -> None:
    if len(values) != len(set(values)):
        raise ValueError("权限策略不能包含重复条目")


class PolicyModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomRole(PolicyModel):
    id: PolicyId
    name: PolicyName
    permissions: list[Permission] = Field(max_length=80)


class UserGroup(PolicyModel):
    id: PolicyId
    name: PolicyName
    role_ids: list[PolicyId] = Field(max_length=32)


class UserAssignment(PolicyModel):
    user_id: AccountId
    role_ids: list[PolicyId] = Field(max_length=32)
    group_ids: list[PolicyId] = Field(max_length=32)
    permissions: list[Permission] = Field(max_length=80)


class AccessPolicyDocument(PolicyModel):
    roles: list[CustomRole] = Field(max_length=128)
    groups: list[UserGroup] = Field(max_length=128)
    users: list[UserAssignment] = Field(max_length=2048)

    @model_validator(mode="after")
    def validate_links(self) -> Self:
        _require_unique([item.id for item in self.roles])
        _require_unique([item.name.casefold() for item in self.roles])
        _require_unique([item.id for item in self.groups])
        _require_unique([item.name.casefold() for item in self.groups])
        _require_unique([item.user_id for item in self.users])
        role_ids = {item.id for item in self.roles}
        group_ids = {item.id for item in self.groups}
        for role in self.roles:
            _require_unique(role.permissions)
        for group in self.groups:
            _require_unique(group.role_ids)
            if not role_ids.issuperset(group.role_ids):
                raise ValueError("用户组引用了不存在的角色")
        for user in self.users:
            _require_unique(user.role_ids)
            _require_unique(user.group_ids)
            _require_unique(user.permissions)
            if not role_ids.issuperset(user.role_ids) or not group_ids.issuperset(user.group_ids):
                raise ValueError("用户引用了不存在的角色或用户组")
        return self


class FeatureCatalogItem(PolicyModel):
    page: str
    label: str
    write: bool


class AccessPolicyResponse(AccessPolicyDocument):
    revision: str
    catalog: list[FeatureCatalogItem]
