import asyncio
import logging
import sys


from automation_server_client import (
    AutomationServer,
    Workqueue,
    WorkItemError,
    Credential,
    WorkItemStatus,
)
from datetime import datetime, timedelta, timezone
from kmd_nexus_client import NexusClientManager
from process.nexus_service import NexusService
from odk_tools.tracking import Tracker

nexus: NexusClientManager
tracker: Tracker
proces_navn = "Udsendelse af supplerende indlæggelsesoplysninger"


def _get_value_case_insensitive(payload: dict, key: str):
    return next(
        (value for k, value in payload.items() if k.lower() == key.lower()), None
    )


def _parse_date(value):
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        normalized = value.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(normalized).date()
        except ValueError:
            try:
                return datetime.strptime(value[:10], "%Y-%m-%d").date()
            except ValueError:
                return None
    return None


async def populate_queue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)
    aktivitetsliste = nexus.aktivitetslister.hent_aktivitetsliste(
        navn="...systembeskeder MedCom - indlæggelsesrapport",
        organisation=None,
        medarbejder=None,
        antal_sider=30,
    )

    cutoff_date = (datetime.now(timezone.utc) - timedelta(days=3)).date()

    filtered_aktivitetsliste = []
    for aktivitet in aktivitetsliste or []:
        status = _get_value_case_insensitive(aktivitet, "status")
        name = _get_value_case_insensitive(aktivitet, "name")
        aktivitet_date = _parse_date(_get_value_case_insensitive(aktivitet, "Date"))

        if status != "Afsendt":
            continue
        if name != "Indlæggelsesrapport - automatisk":
            continue
        if aktivitet_date is None or aktivitet_date < cutoff_date:
            continue

        filtered_aktivitetsliste.append(aktivitet)

    if filtered_aktivitetsliste:
        for aktivitet in filtered_aktivitetsliste:
            eksisterende_kødata = workqueue.get_item_by_reference(str(aktivitet["id"]))

            if len(eksisterende_kødata) > 0:
                continue

            workqueue.add_item(aktivitet, str(aktivitet["id"]))


async def process_workqueue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)
    besked_tekst = ""

    for item in workqueue:
        with item:
            data = item.data  # Item data deserialized from json as dict

            try:
                besked_reference = nexus.hent_fra_reference(data)
                besked = nexus.medcom.hent_besked(besked_reference)

                if besked is None:
                    continue

                xml = nexus.medcom.dekoder_medcom_xml(besked)
                modtager = nexus_service._extract_receiver_json(xml).get(
                    "EANIdentifier"
                )

                if modtager is None:
                    continue

                # Hent generelle oplysninger
                borger = nexus.borgere.hent_borger(
                    data["patients"][0]["patientIdentifier"]["identifier"]
                )

                if borger is None:
                    continue

                pathway = nexus.borgere.hent_visning(borger=borger)

                # Hent generelle oplysninger og genoplivningsoplysninger
                tekst, genoplivnings_skemaer = nexus_service.hent_generelle_oplysninger(
                    pathway
                )
                besked_tekst += tekst

                # Hent handlingsanvisninger
                tekst = nexus_service.hent_handlingsanvisninger(pathway)
                besked_tekst += tekst

                # Hent oplysninger om genoplivning
                tekst = nexus_service.hent_oplysninger_om_genoplivning(
                    genoplivnings_skemaer
                )
                besked_tekst += tekst

                # Send besked
                if len(besked_tekst.strip()) == 0:
                    tracker.track_partial_task(proces_navn)
                    continue

                nexus_service.send_besked(besked_tekst, borger, modtager, proces_navn)

            except WorkItemError as e:
                # A WorkItemError represents a soft error that indicates the item should be passed to manual processing or a business logic fault
                logger.error(f"Error processing item: {data}. Error: {e}")
                item.fail(str(e))


if __name__ == "__main__":
    ats = AutomationServer.from_environment()
    workqueue = ats.workqueue()

    nexus_credential = Credential.get_credential("KMD Nexus - produktion")
    tracking_credential = Credential.get_credential("Odense SQL Server")

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    nexus = NexusClientManager(
        client_id=nexus_credential.username,
        client_secret=nexus_credential.password,
        instance=nexus_credential.data["instance"],
    )

    tracker = Tracker(
        username=tracking_credential.username, password=tracking_credential.password
    )

    nexus_service = NexusService(nexus=nexus, tracker=tracker)

    # Queue management
    if "--queue" in sys.argv:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        asyncio.run(populate_queue(workqueue))
        exit(0)

    # Process workqueue
    asyncio.run(process_workqueue(workqueue))
