from datetime import datetime, timezone
from json import dumps
from typing import Dict, List, Optional

from ..helpers import dict_drop_blanks
from .ggroup import GoogleGroup


def section(
    text: str = None,
    fields: List[Dict[str, any]] = None,
    block_id: str = None,
    accessory: Dict[str, any] = None,
) -> Dict[str, any]:
    data = {
        "type": "section",
        "text": text,
        "fields": fields,
        "block_id": block_id,
        "accessory": accessory,
    }
    return dict_drop_blanks(data)


def context(*elements: List[Dict[str, any]]) -> Dict[str, any]:
    return {"type": "context", "elements": elements}


def actions(block_id: str, *elements: List[Dict[str, str]]) -> Dict[str, any]:
    return {"type": "actions", "block_id": block_id, "elements": elements}


def divider() -> Dict[str, str]:
    return {"type": "divider"}


def md(text: str) -> Dict[str, str]:
    return {"type": "mrkdwn", "text": text}


def text(text: str, emoji: bool = None) -> Dict[str, str]:
    # Some text fields don't support the emoji field at all
    return dict_drop_blanks({"type": "plain_text", "text": text, "emoji": emoji})


def button(
    action_id: str,
    style: str,
    text: Dict[str, str],
    value: Dict[str, any],
    confirm: Dict[str, any] = None,
) -> Dict[str, str]:
    data = dict_drop_blanks(
        {
            "type": "button",
            "action_id": action_id,
            "text": text,
            "value": dumps(value),
            "confirm": confirm,
            # Style can be empty, which creates "default" style buttons
            "style": style,
        }
    )

    return data


def confirm(message: str) -> Dict[str, Dict[str, str]]:
    return {
        "title": text("Are you sure?"),
        "text": md(message),
        "confirm": text("Confirm"),
        "deny": text("Cancel"),
    }


def inputsection(
    label: Dict[str, str], element: Dict[str, any], **extras: Dict[str, any]
) -> Dict[str, any]:
    return {
        **extras,
        "type": "input",
        "label": label,
        "element": element,
    }


def inputbox(
    action_id: str, input_type: str, placeholder: Dict[str, str], **extras: Dict[str, any]
) -> Dict[str, any]:
    return {**extras, "action_id": action_id, "type": input_type, "placeholder": placeholder}


def checkboxes(
    action_id: str, options: List[Dict[str, any]], **extras: Dict[str, any]
) -> Dict[str, any]:
    return {**extras, "action_id": action_id, "type": "checkboxes", "options": options}


def checkbox(value: str, text: Dict[str, str]) -> Dict[str, any]:
    return {"value": value, "text": text}


def find_block(blocks: List[Dict[str, any]], block_id: Optional[str]) -> Optional[Dict[str, any]]:
    for block in blocks:
        if block["block_id"] == block_id:
            return block
        if "elements" in block:
            subblock = find_block(block["elements"], block_id)
            if subblock:
                return subblock


def remove_blocks(blocks: List[Dict[str, any]], block_ids: List[str]) -> List[Dict[str, any]]:
    return [block for block in blocks if block["block_id"] not in block_ids]


def remove_actions(elements: List[Dict[str, any]], action_ids: List[str]) -> List[Dict[str, any]]:
    return [action for action in elements if action["action_id"] not in action_ids]


def group_info(group: GoogleGroup) -> List[Dict[str, any]]:
    group_owners = "• " + ("\n• ".join(member.email for member in group.owners) or "None")
    return section(
        fields=[
            md(f"*Name:*\n{group.name}"),
            md(f"*Members:*\n{len(group.members)}"),
            md(f"*Description:*\n{group.description or 'None'}"),
            md(f"*Owners:*\n{group_owners}"),
            md(f"*Protected:*\n{group.protected}"),
        ]
    )


def timestamp(ts: float) -> str:
    dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    return (
        f"<!date^{int(ts)}"
        "^{date_short_pretty} at {time}"
        f"|{dt.strftime('on %b %d at %H:%M UTC')}>"
    )
