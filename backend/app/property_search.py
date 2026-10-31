"""Validated, literal search conditions. Column names never come from raw SQL input."""
import re
from typing import Literal

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

SEARCH_FIELDS = ("owner_name", "address", "phone_01", "phone_02", "ic_number", "remark")


def validate_clue(value: str, operator: str = "contains") -> str:
    value = value.strip()
    if len(value) < 3:
        raise ValueError("Enter at least three characters for each search clue")
    if operator in ("contains", "ends_with") and not re.search(r"[^\W_]{3}", value):
        raise ValueError("Contains and ends with need at least three consecutive letters or digits")
    return value


class SearchCondition(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    field: Literal["owner_name", "address"]
    operator: Literal["contains", "starts_with", "ends_with", "equals"]
    value: str = Field(min_length=3, max_length=200)

    @model_validator(mode="after")
    def validate_fragment(self):
        validate_clue(self.value, self.operator)
        return self


class SearchBuilder(BaseModel):
    model_config = ConfigDict(extra="forbid")
    match: Literal["all", "any"] = "all"
    conditions: list[SearchCondition] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def limit_conditions_per_category(self):
        for field in ("owner_name", "address"):
            if sum(row.field == field for row in self.conditions) > 3:
                raise ValueError("Use at most three conditions per category (name or address)")
        return self


def escape_like(value: str) -> str:
    return value.replace("!", "!!").replace("%", "!%").replace("_", "!_")


def quick_search_clause(search: str | None, *, preview: bool = False) -> tuple[str, list[str]]:
    if search is None:
        return "", []
    try:
        value = validate_clue(search)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from None
    pattern = "%" + escape_like(value) + "%"
    if preview:
        return "address ILIKE %s ESCAPE '!'", [pattern]
    fields = " OR ".join(f"{field} ILIKE %s ESCAPE '!'" for field in SEARCH_FIELDS)
    return f"({fields})", [pattern] * len(SEARCH_FIELDS)


def builder_clause(raw: str | None, *, preview: bool = False) -> tuple[str, list[str]]:
    if raw is None:
        return "", []
    try:
        builder = SearchBuilder.model_validate_json(raw)
    except ValidationError:
        raise HTTPException(status_code=422, detail="Invalid search builder: use up to six conditions total and three per category (name or address). Every clue needs at least three characters; contains/ends with need three consecutive letters or digits.") from None
    if preview and any(row.field == "owner_name" for row in builder.conditions):
        raise HTTPException(status_code=403, detail="Owner-name search requires search credits")
    parts, values = [], []
    for row in builder.conditions:
        # Both field and operator have been validated against fixed allowlists.
        if row.operator == "equals":
            # Use the plain-column GIN to narrow candidates, then retain the
            # original comparison so case handling and literal matching stay
            # consistent with existing searches.
            parts.append(f"({row.field} ILIKE %s ESCAPE '!' AND LOWER({row.field}) = LOWER(%s))")
            values.extend([escape_like(row.value), row.value])
        elif row.operator == "starts_with":
            pattern = escape_like(row.value) + "%"
            parts.append(f"({row.field} ILIKE %s ESCAPE '!' AND LOWER({row.field}) LIKE LOWER(%s) ESCAPE '!')")
            values.extend([pattern, pattern])
        else:
            parts.append(f"{row.field} ILIKE %s ESCAPE '!'")
            values.append("%" + escape_like(row.value)
                          + ("%" if row.operator == "contains" else ""))
    return "(" + (" AND " if builder.match == "all" else " OR ").join(parts) + ")", values
