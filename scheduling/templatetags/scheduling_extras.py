import segno
from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter
def get_item(mapping, key):
    return mapping.get(key)


@register.simple_tag
def qr_svg(url, title=''):
    code = segno.make(url, error='m')
    # Always black on white: inverted codes are unreadable for many scanners
    svg = code.svg_inline(
        scale=1,
        border=2,
        dark='#000',
        light='#fff',
        title=title or None,
        omitsize=True,
    )
    return mark_safe(svg)
