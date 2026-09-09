import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.loadouts import expand_loadout_rows, loadout_to_yaml, loadouts_from_yaml
from app.models.db import Job, Loadout, NetBoxInstance, Record
from app.netbox.client import NetBoxClient
from app.templates_config import templates

log = logging.getLogger(__name__)

router = APIRouter()

# Separator between manufacturer and model in form select values; NetBox
# names allow most characters, so use a sequence that cannot appear in them
# (NetBox limits names to 100 chars of printable text, "||" is legal but
# vanishingly unlikely — reject it at save time rather than mis-split).
SEP = "||"
BAY_FIELD_PREFIX = "bay::"


def _instances(db: Session) -> list[NetBoxInstance]:
    return db.execute(select(NetBoxInstance).order_by(NetBoxInstance.name)).scalars().all()


def _loadouts(db: Session) -> list[Loadout]:
    return db.execute(select(Loadout).order_by(Loadout.name)).scalars().all()


def _instance_client(db: Session, instance_id: str) -> tuple[NetBoxInstance | None, NetBoxClient | None]:
    try:
        instance = db.get(NetBoxInstance, uuid.UUID(instance_id))
    except (ValueError, TypeError):
        instance = None
    if not instance:
        return None, None
    return instance, NetBoxClient(instance.url, instance.token)


def _page(request: Request, db: Session, **extra):
    return templates.TemplateResponse("loadouts.html", {
        "request": request,
        "instances": _instances(db),
        "loadouts": _loadouts(db),
        **extra,
    })


@router.get("/loadouts", response_class=HTMLResponse)
def loadouts_page(request: Request, db: Session = Depends(get_db)):
    return _page(request, db)


@router.get("/loadouts/device-types", response_class=HTMLResponse)
def device_types_partial(request: Request, instance_id: str = "", db: Session = Depends(get_db)):
    """HTMX partial: device types (grouped by manufacturer) on one NetBox instance."""
    instance, client = _instance_client(db, instance_id)
    if not client:
        return HTMLResponse('<p class="text-sm text-red-600">Select a saved NetBox instance first.</p>')
    try:
        device_types = sorted(
            ((dt.manufacturer.name, dt.model) for dt in client.nb.dcim.device_types.all()),
            key=lambda pair: (pair[0].lower(), pair[1].lower()),
        )
    except Exception as exc:
        log.warning(f"Failed to list device types from {instance.name}: {exc}")
        return HTMLResponse(f'<p class="text-sm text-red-600">Could not reach NetBox: {exc}</p>')
    return templates.TemplateResponse("partials/loadout_device_types.html", {
        "request": request,
        "instance_id": instance_id,
        "device_types": device_types,
        "sep": SEP,
    })


@router.get("/loadouts/bays", response_class=HTMLResponse)
def bays_partial(request: Request, instance_id: str = "", device_type_value: str = "",
                 db: Session = Depends(get_db)):
    """HTMX partial: the chosen device type's module bays with module-type dropdowns."""
    instance, client = _instance_client(db, instance_id)
    if not client or SEP not in device_type_value:
        return HTMLResponse("")
    manufacturer_name, model = device_type_value.split(SEP, 1)
    try:
        manufacturer = client.nb.dcim.manufacturers.get(name=manufacturer_name)
        dt = client.nb.dcim.device_types.get(model=model, manufacturer_id=manufacturer.id) if manufacturer else None
        if not dt:
            return HTMLResponse(f'<p class="text-sm text-red-600">Device type {model!r} not found.</p>')
        bays = sorted((b.name for b in client.nb.dcim.module_bay_templates.filter(device_type_id=dt.id)))
        module_types = sorted(
            ((mt.manufacturer.name, mt.model) for mt in client.nb.dcim.module_types.all()),
            key=lambda pair: (pair[0].lower(), pair[1].lower()),
        )
    except Exception as exc:
        log.warning(f"Failed to list bays/module types from {instance.name}: {exc}")
        return HTMLResponse(f'<p class="text-sm text-red-600">Could not reach NetBox: {exc}</p>')
    return templates.TemplateResponse("partials/loadout_bays.html", {
        "request": request,
        "manufacturer": manufacturer_name,
        "model": model,
        "bays": bays,
        "module_types": module_types,
        "sep": SEP,
        "bay_field_prefix": BAY_FIELD_PREFIX,
    })


@router.post("/loadouts", response_class=HTMLResponse)
async def create_loadout(request: Request, db: Session = Depends(get_db)):
    form = await request.form()
    name = str(form.get("loadout_name") or "").strip()
    device_type = str(form.get("device_type_value") or "")
    if not name:
        return _page(request, db, error="Give the loadout a name")
    if SEP not in device_type:
        return _page(request, db, error="Pick a NetBox instance and device type, then fill in the bays")
    manufacturer, model = device_type.split(SEP, 1)

    bays: dict = {}
    for field, value in form.multi_items():
        if not field.startswith(BAY_FIELD_PREFIX) or not str(value):
            continue
        fit = str(value)
        if SEP not in fit:
            return _page(request, db, error=f"Invalid module selection for bay '{field[len(BAY_FIELD_PREFIX):]}'")
        fit_manufacturer, fit_model = fit.split(SEP, 1)
        bays[field[len(BAY_FIELD_PREFIX):]] = {
            "manufacturer": fit_manufacturer, "module_type": fit_model,
        }
    if not bays:
        return _page(request, db, error="A loadout needs at least one bay fitted with a module type")
    if db.execute(select(Loadout).where(Loadout.name == name)).scalar_one_or_none():
        return _page(request, db, error=f"A loadout named '{name}' already exists")

    db.add(Loadout(name=name, manufacturer=manufacturer, device_type=model, bays=bays))
    db.commit()
    return RedirectResponse(url="/loadouts", status_code=303)


@router.post("/loadouts/import", response_class=HTMLResponse)
async def import_loadouts(request: Request, yaml_file: UploadFile, db: Session = Depends(get_db)):
    text = (await yaml_file.read()).decode("utf-8", errors="replace")
    try:
        parsed = loadouts_from_yaml(text)
    except ValueError as exc:
        return _page(request, db, error=str(exc))
    if not parsed:
        return _page(request, db, error="No loadouts found in the file")
    for entry in parsed:
        if db.execute(select(Loadout).where(Loadout.name == entry["name"])).scalar_one_or_none():
            return _page(request, db, error=f"A loadout named '{entry['name']}' already exists")
        db.add(Loadout(**entry))
    db.commit()
    return RedirectResponse(url="/loadouts", status_code=303)


@router.get("/loadouts/{loadout_id}/export")
def export_loadout(loadout_id: uuid.UUID, db: Session = Depends(get_db)):
    loadout = db.get(Loadout, loadout_id)
    if not loadout:
        return Response(status_code=404)
    filename = loadout.name.lower().replace(" ", "-")
    return Response(
        content=loadout_to_yaml(loadout),
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{filename}.yaml"'},
    )


@router.post("/loadouts/{loadout_id}/duplicate", response_class=HTMLResponse)
def duplicate_loadout(request: Request, loadout_id: uuid.UUID,
                      new_name: Annotated[str, Form()] = "", db: Session = Depends(get_db)):
    loadout = db.get(Loadout, loadout_id)
    if not loadout:
        return _page(request, db, error="Loadout not found")
    name = new_name.strip() or f"{loadout.name} (copy)"
    if db.execute(select(Loadout).where(Loadout.name == name)).scalar_one_or_none():
        return _page(request, db, error=f"A loadout named '{name}' already exists")
    db.add(Loadout(name=name, manufacturer=loadout.manufacturer,
                   device_type=loadout.device_type, bays=dict(loadout.bays)))
    db.commit()
    return RedirectResponse(url="/loadouts", status_code=303)


@router.post("/loadouts/{loadout_id}/delete", response_class=HTMLResponse)
def delete_loadout(request: Request, loadout_id: uuid.UUID, db: Session = Depends(get_db)):
    loadout = db.get(Loadout, loadout_id)
    if loadout:
        db.delete(loadout)
        db.commit()
    return RedirectResponse(url="/loadouts", status_code=303)


@router.get("/loadouts/{loadout_id}/devices", response_class=HTMLResponse)
def devices_partial(request: Request, loadout_id: uuid.UUID, instance_id: str = "",
                    db: Session = Depends(get_db)):
    """HTMX partial: devices of the loadout's device type on one instance."""
    loadout = db.get(Loadout, loadout_id)
    instance, client = _instance_client(db, instance_id)
    if not loadout or not client:
        return HTMLResponse('<p class="text-sm text-red-600">Select a saved NetBox instance first.</p>')
    try:
        manufacturer = client.nb.dcim.manufacturers.get(name=loadout.manufacturer)
        dt = client.nb.dcim.device_types.get(
            model=loadout.device_type, manufacturer_id=manufacturer.id) if manufacturer else None
        devices = sorted(
            ((d.name, d.site.name) for d in client.nb.dcim.devices.filter(device_type_id=dt.id) if d.name and d.site),
            key=lambda pair: pair[0].lower(),
        ) if dt else []
    except Exception as exc:
        log.warning(f"Failed to list devices from {instance.name}: {exc}")
        return HTMLResponse(f'<p class="text-sm text-red-600">Could not reach NetBox: {exc}</p>')
    return templates.TemplateResponse("partials/loadout_devices.html", {
        "request": request,
        "loadout": loadout,
        "devices": devices,
        "sep": SEP,
        "device_type_known": dt is not None,
    })


@router.post("/loadouts/{loadout_id}/apply", response_class=HTMLResponse)
async def apply_loadout(request: Request, loadout_id: uuid.UUID, db: Session = Depends(get_db)):
    form = await request.form()
    loadout = db.get(Loadout, loadout_id)
    if not loadout:
        return _page(request, db, error="Loadout not found")
    instance, _ = _instance_client(db, str(form.get("instance_id") or ""))
    if not instance:
        return _page(request, db, error="Select a saved NetBox instance to apply to")

    targets = []
    for value in form.getlist("target_devices"):
        value = str(value)
        if SEP not in value:
            return _page(request, db, error=f"Invalid device selection: {value!r}")
        device, site = value.split(SEP, 1)
        targets.append({"device": device, "site": site})
    if not targets:
        return _page(request, db, error="Select at least one device to apply the loadout to")

    status = str(form.get("status") or "active")
    rows = expand_loadout_rows(loadout.bays, targets, status=status)

    job = Job(
        id=uuid.uuid4(),
        name=f"Apply loadout '{loadout.name}' ({len(targets)} device(s))",
        file_type="modules",
        status="pending",
        total_records=len(rows),
        netbox_url=instance.url,
        netbox_token=instance.token,
    )
    db.add(job)
    db.flush()
    for i, row in enumerate(rows, start=1):
        db.add(Record(job_id=job.id, row_number=i, raw_data=row))
    db.commit()
    return RedirectResponse(url=f"/jobs/{job.id}", status_code=303)
