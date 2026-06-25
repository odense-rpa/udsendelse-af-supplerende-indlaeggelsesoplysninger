import asyncio
import logging
import sys
import xml.etree.ElementTree as ET

from automation_server_client import (
    AutomationServer,
    Workqueue,
    WorkItemError,
    Credential,
    WorkItemStatus,
)
from datetime import datetime, timedelta, timezone
from kmd_nexus_client import NexusClientManager
from kmd_nexus_client.tree_helpers import filter_by_path
from odk_tools.tracking import Tracker

nexus: NexusClientManager
tracker: Tracker
proces_navn = "Udsendelse af supplerende indlæggelsesoplysninger"

def _get_value_case_insensitive(payload: dict, key: str):
        return next((value for k, value in payload.items() if k.lower() == key.lower()), None)

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


def _xml_element_to_dict(element: ET.Element):
    """Convert an XML element subtree to a plain dictionary."""
    payload = {}

    # Include element attributes when present.
    if element.attrib:
        payload["@attributes"] = dict(element.attrib)

    children = list(element)
    if children:
        for child in children:
            child_key = child.tag.split("}", 1)[-1]
            child_payload = _xml_element_to_dict(child)
            if child_key in payload:
                if not isinstance(payload[child_key], list):
                    payload[child_key] = [payload[child_key]]
                payload[child_key].append(child_payload)
            else:
                payload[child_key] = child_payload
    else:
        text = (element.text or "").strip()
        if text:
            return text

    return payload


def _format_date_as_ddmmyyyy(value):
    """Format a date value as 'dd/MM/yyyy'. Handles ISO strings and datetime objects."""
    if value is None:
        return ""
    
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            normalized = value.replace("Z", "+00:00")
            try:
                dt = datetime.fromisoformat(normalized)
            except ValueError:
                try:
                    dt = datetime.strptime(value[:10], "%Y-%m-%d")
                except ValueError:
                    return value
        else:
            return str(value)
        
        return dt.strftime("%d/%m/%Y")
    except Exception:
        return str(value)


def _extract_receiver_json(xml_payload):
    if not xml_payload:
        return None

    if isinstance(xml_payload, bytes):
        xml_payload = xml_payload.decode("utf-8", errors="replace")

    root = ET.fromstring(xml_payload)
    receiver = root.find(".//{urn:oio:medcom:municipality:1.0.0}Receiver")
    if receiver is None:
        return None

    receiver_data = _xml_element_to_dict(receiver)
    return receiver_data

async def populate_queue(workqueue: Workqueue):
    logger = logging.getLogger(__name__)
    aktivitetsliste = nexus.aktivitetslister.hent_aktivitetsliste(
        navn="...systembeskeder MedCom - indlæggelsesrapport", 
        organisation=None,
        medarbejder=None,
        antal_sider=30
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
    
    afsender = "Odense Kommune - EAN:5790000121441 - Ørbækvej 100B, 5220 Odense SØ"
    generelle_oplysninger_felter = ["Mestring", "Helbredsoplysninger", "Bolig", "Netværk", "Ønsker for den sidste tid"]
    godkendte_indsatser = [
        "Dag - Administrative og strukturelle opgaver (kompenserende støtte)",
        "Dag - Indtagelse af mad og drikke (kompenserende støtte)",
        "Dag - Mobilitet (kompenserende støtte)",
        "Dag - Personlig hygiejne (kompenserende støtte)",
        "Dag - Udskillelser (kompenserende støtte)",
        "Aften - Administrative og strukturelle opgaver (kompenserende støtte)",
        "Aften - Indtagelse af mad og drikke (kompenserende støtte)",
        "Aften - Mobilitet (kompenserende støtte)",
        "Aften - Personlig hygiejne (kompenserende støtte)",
        "Aften - Udskillelser (kompenserende støtte)",
        "Nat - Administrative og strukturelle opgaver (kompenserende støtte)",
        "Nat - Indtagelse af mad og drikke (kompenserende støtte)",
        "Nat - Mobilitet (kompenserende støtte)",
        "Nat - Personlig hygiejne (kompenserende støtte)",
        "Nat - Udskillelser (kompenserende støtte)",
        "Dag - Administrative og strukturelle opgaver (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Indtagelse af mad og drikke (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Mobilitet (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Personlig hygiejne (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Udskillelser (tidsafgrænset rehabiliteringsforløb)",
        "Aften - Administrative og strukturelle opgaver (tidsafgrænset rehabiliteringsforløb)",
        "Aften - Indtagelse af mad og drikke (tidsafgrænset rehabiliteringsforløb)",
        "Aften - Mobilitet (tidsafgrænset rehabiliteringsforløb)",
        "Aften - Personlig hygiejne (tidsafgrænset rehabiliteringsforløb)",
        "Aften - Udskillelser (tidsafgrænset rehabiliteringsforløb)",
        "Nat - Administrative og strukturelle opgaver (tidsafgrænset rehabiliteringsforløb)",
        "Nat - Indtagelse af mad og drikke (tidsafgrænset rehabiliteringsforløb)",
        "Nat - Mobilitet (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Administrative og strukturelle opgaver - ÆL § 9",
        "Dag - Indtagelse af mad og drikke - ÆL § 9",
        "Nat - Personlig hygiejne (tidsafgrænset rehabiliteringsforløb)",
        "Nat - Udskillelser (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Mobilitet - ÆL § 9",
        "Dag - Personlig hygiejne - ÆL § 9",
        "Dag - Udskillelser - ÆL § 9",
        "Dag - Tilberede og anrette mad - ÆL § 9",
        "Dag - Bestillling af varer og sætte varer på plads - ÆL § 9",
        "Aften - Administrative og strukturelle opgaver - ÆL § 9",
        "Aften - Indtagelse af mad og drikke - ÆL § 9",
        "Aften - Mobilitet  - ÆL § 9",
        "Aften - Personlig hygiejne - ÆL § 9",
        "Aften - Udskillelser - ÆL § 9",
        "Aften - Tilberede og anrette mad - ÆL § 9",
        "Nat - Administrative og strukturelle opgaver - ÆL § 9",
        "Nat - Indtagelse af mad og drikke - ÆL § 9",
        "Nat - Mobilitet - ÆL § 9",
        "Nat - Personlig hygiejne  - ÆL § 9",
        "Nat - Udskillelser  - ÆL § 9",
        "Dag - Tilberede/anrette mad (tidsafgrænset rehabiliteringsforløb)",
        "Aften - Tilberede/anrette mad (tidsafgrænset rehabiliteringsforløb)",
        "Dag - Tilberede og anrette mad (kompenserende støtte)",
        "Aften - Tilberede og anrette mad (kompenserende støtte)",
    ]
    besked_tekst = ""

    for item in workqueue:
        with item:
            data = item.data  # Item data deserialized from json as dict
 
            try:                
                besked_reference = nexus.hent_fra_reference(data)
                besked = nexus.medcom.hent_besked(besked_reference)
                xml = nexus.medcom.dekoder_medcom_xml(besked)
                modtager = _extract_receiver_json(xml).get("EANIdentifier")

                if modtager is None:
                    continue                

                # Hent generelle oplysninger
                borger = nexus.borgere.hent_borger(data["patients"][0]["patientIdentifier"]["identifier"])

                if borger is None:
                    continue

                pathway = nexus.borgere.hent_visning(borger=borger)
                
                aktiviteter = nexus.nexus_client.get(
                    pathway["_links"]["patientActivities"]["href"]
                ).json()

                generelle_skemaer = [
                    ref for ref in aktiviteter if ref.get("patientActivityType") == "formData"
                    and ref.get("formDefinition", {}).get("title") in ["Generelle oplysninger - V. 2025", "Helbredsoplysninger"]
                    and ref.get("workflowState", {}).get("name") == "Aktivt"
                ]

                genoplivnings_skemaer = [
                    ref for ref in aktiviteter if ref.get("patientActivityType") == "formData"
                    and ref.get("formDefinition", {}).get("title") == "Fravalg af genoplivningsforsøg"                    
                ]
                
                for skema_reference in generelle_skemaer:
                    skema = nexus.hent_fra_reference(skema_reference)
                    for item in skema["items"]:
                        if item["label"] in generelle_oplysninger_felter:
                            if item.get("value") is None or str(item.get("value")).strip() == "":
                                continue
                            
                            besked_tekst += f"{item['label']}:\n"                            
                            besked_tekst += item['value'].replace("\n", "\r\n").strip() + "\r\n"
                            formatted_date = _format_date_as_ddmmyyyy(skema['lastStateChange'])
                            besked_tekst += f"Sidst opd. {formatted_date}\r\n\r\n"


                # Hent handlingsanvisninger
                referencer = nexus.borgere.hent_referencer(pathway)                
                
                skema_referencer = filter_by_path(
                    referencer,
                    "/*/*/formDataV2Reference",
                    active_pathways_only=True,
                )

                skema_referencer = [
                    ref for ref in skema_referencer if ref.get("patientActivityType") == "formData"
                    and "Handlingsanvisning" in (ref.get("formDefinition", {}).get("title", "") or "")
                    and ref.get("workflowState", {}).get("name") == "Aktivt"
                ]

                for skema_reference in skema_referencer:
                    skema = nexus.hent_fra_reference(skema_reference)
                    relaterede_aktiviteter = nexus.nexus_client.get(skema["_links"]["relatedActivities"]["href"]).json()

                    for relateret_aktivitet in relaterede_aktiviteter:
                        for aktivitet in relateret_aktivitet.get("citizenActivitiesGroups", []).get("activities", []):                            
                            if aktivitet.get("activityReference", {}).get("name") in godkendte_indsatser:
                                
                                for item in skema["items"]:
                                    label_tekst = (item.get("label") or "").strip()
                                    value_tekst = str(item.get("value") or "").replace("\n", "\r\n").strip()
                                    besked_tekst += f"\r\n{label_tekst}: {value_tekst}\r\n"
                
                
                # Hent oplysninger om genoplivning
                if len(genoplivnings_skemaer) > 0:
                    skema = nexus.hent_fra_reference(genoplivnings_skemaer[0])
                    for item in skema["items"]:
                        if item["label"] == "Tekst":
                            tekst = str(item.get("value") or "").replace("\n", "\r\n").strip()
                            if tekst:
                                besked_tekst += f"{tekst}\r\n"
                        elif item["label"] == "Dato":                            
                            formatted_date = _format_date_as_ddmmyyyy(item['value'])
                            besked_tekst += f"Dato: {formatted_date}\r\n"

                # Send besked
                if len(besked_tekst.strip()) == 0:
                    tracker.track_partial_task(proces_navn)
                    continue

                endelig_tekst = "Dette er en automatisk besked med supplerende indlæggelsesoplysninger. Ved behov for kontakt til borgers hjemmepleje kan du finde et tlf. nummer i  indlæggelsesrapporten."
                endelig_tekst += f"\r\n\r\n{besked_tekst}"

                nexus.medcom.send_besked(
                    borger=borger,
                    fra=afsender,
                    til=modtager,                    
                    tekst=endelig_tekst,
                    emne="Supplerende indlæggelsesoplysninger"
                )

                tracker.track_task(proces_navn)
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

    # Queue management
    if "--queue" in sys.argv:
        workqueue.clear_workqueue(WorkItemStatus.NEW)
        asyncio.run(populate_queue(workqueue))
        exit(0)

    # Process workqueue
    asyncio.run(process_workqueue(workqueue))
