from django import template
register = template.Library()

@register.filter
def get_item(dictionary, key):
    # Support tuple keys if needed, but template syntax is limited.
    # We constructed matrix keys as (date, shop_id).
    # In template we do: matrix|get_item:date|get_item:shop.id
    # But wait, date object as key in template is tricky.
    # Let's adjust view to use strings or nested dicts if possible.
    # Or just handle the lookup here.

    # If dictionary is a dict
    if isinstance(dictionary, dict):
        return dictionary.get(key)
    return None
