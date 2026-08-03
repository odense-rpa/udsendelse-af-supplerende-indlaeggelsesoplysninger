from datetime import datetime
from kmd_nexus_client import NexusClientManager
from kmd_nexus_client.tree_helpers import (
    filter_by_path,
)
from odk_tools.tracking import Tracker
from process.config import get_excel_mapping

import xml.etree.ElementTree as ET


afsender = "Odense Kommune - EAN:5790000121441 - Ørbækvej 100B, 5220 Odense SØ"
generelle_oplysninger_felter = [
    "Mestring",
    "Helbredsoplysninger",
    "Bolig",
    "Netværk",
    "Ønsker for den sidste tid",
]


class NexusService:
    def __init__(self, nexus: NexusClientManager, tracker: Tracker):
        self.nexus = nexus
        self.tracker = tracker

    def _xml_element_to_dict(self, element: ET.Element):
        """Convert an XML element subtree to a plain dictionary."""
        payload = {}

        # Include element attributes when present.
        if element.attrib:
            payload["@attributes"] = dict(element.attrib)

        children = list(element)
        if children:
            for child in children:
                child_key = child.tag.split("}", 1)[-1]
                child_payload = self._xml_element_to_dict(child)
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

    def _format_date_as_ddmmyyyy(self, value):
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

            return dt.strftime("%d-%m-%Y")
        except Exception:
            return str(value)

    def _extract_receiver_json(self, xml_payload):
        if not xml_payload:
            return None

        if isinstance(xml_payload, bytes):
            xml_payload = xml_payload.decode("utf-8", errors="replace")

        root = ET.fromstring(xml_payload)
        receiver = root.find(".//{urn:oio:medcom:municipality:1.0.0}Receiver")
        if receiver is None:
            return None

        receiver_data = self._xml_element_to_dict(receiver)
        return receiver_data

    def hent_generelle_oplysninger(self, pathway):
        """
        Extract general information (generelle oplysninger) from schemas and return formatted text.
        Also retrieves resuscitation schemas for later use.

        Args:
            pathway: The patient pathway object

        Returns:
            tuple: (formatted_text, genoplivnings_skemaer) where formatted_text is the text to append
                   and genoplivnings_skemaer is a list of resuscitation schemas
        """
        tekst = ""

        aktiviteter = self.nexus.nexus_client.get(
            pathway["_links"]["patientActivities"]["href"]
        ).json()

        generelle_skemaer = [
            ref
            for ref in aktiviteter
            if ref.get("patientActivityType") == "formData"
            and ref.get("formDefinition", {}).get("title")
            in ["Generelle oplysninger - V. 2025", "Helbredsoplysninger"]
            and ref.get("workflowState", {}).get("name") == "Aktivt"
        ]

        genoplivnings_skemaer = [
            ref
            for ref in aktiviteter
            if ref.get("patientActivityType") == "formData"
            and ref.get("formDefinition", {}).get("title")
            == "Fravalg af genoplivningsforsøg"
        ]

        cfs_skemaer = [
            ref
            for ref in aktiviteter
            if ref.get("patientActivityType") == "formData"
            and ref.get("formDefinition", {}).get("title")
            == "Clinical Frailty Scale (CFS) v2"
        ]

        for skema_reference in generelle_skemaer:
            skema = self.nexus.hent_fra_reference(skema_reference)
            for item in skema["items"]:
                if item["label"] in generelle_oplysninger_felter:
                    if (
                        item.get("value") is None
                        or str(item.get("value")).strip() == ""
                    ):
                        continue

                    tekst += f"{item['label']}:\n"
                    tekst += item["value"].replace("\n", "\r\n").strip() + "\r\n"
                    formatted_date = self._format_date_as_ddmmyyyy(
                        skema["lastStateChange"]
                    )
                    tekst += f"Sidst opd. {formatted_date}\r\n\r\n"

        return tekst, genoplivnings_skemaer, cfs_skemaer

    def hent_handlingsanvisninger(self, pathway):
        """
        Extract action guidelines (handlingsanvisninger) from schemas and return formatted text.

        Args:
            pathway: The patient pathway object

        Returns:
            str: Formatted text with action guidelines
        """

        godkendte_indsatser = get_excel_mapping()
        godkendte_indsatsnavne = {
            str(row.get("Indsatsnavn") or "").strip()
            for row in godkendte_indsatser
            if str(row.get("Indsatsnavn") or "").strip()
        }
        tekst = ""
        referencer = self.nexus.borgere.hent_referencer(pathway)

        skema_referencer = filter_by_path(
            referencer,
            "/*/*/formDataV2Reference",
            active_pathways_only=True,
        )

        skema_referencer = [
            ref
            for ref in skema_referencer
            if "Handlingsanvisning" in (ref.get("name", {}) or "")
            and ref.get("workflowState", {}).get("name") == "Aktivt"
        ]

        for skema_reference in skema_referencer:
            skema = self.nexus.hent_fra_reference(skema_reference)
            relaterede_aktiviteter = self.nexus.nexus_client.get(
                skema["_links"]["relatedActivities"]["href"]
            ).json()

            for relateret_aktivitet in relaterede_aktiviteter:
                if relateret_aktivitet.get("groupName") != "Indsatser":
                    continue
                
                citizen_activities_groups = relateret_aktivitet.get(
                    "citizenActivitiesGroups", []
                )
                for aktiviteteter in citizen_activities_groups:
                    aktiviteter = aktiviteteter.get("activities", {})
                    for aktivitet in aktiviteter:
                        if (
                            aktivitet.get("activityReference", {}).get("name")
                            in godkendte_indsatsnavne
                        ):
                            for item in skema["items"]:
                                if (
                                    item.get("value") is None
                                    or item.get("type") == "radioGroup"
                                    or str(item.get("value")).strip() == ""
                                    or item.get("label") == "Uddelegeret til?"
                                ):
                                    continue

                                label_tekst = (item.get("label") or "").strip()
                                value_tekst = (
                                    str(item.get("value") or "")
                                    .replace("\n", "\r\n")
                                    .strip()
                                )
                                tekst += f"\r\n{label_tekst}: {value_tekst}\r\n"
                            break


        return tekst

    def hent_oplysninger_om_genoplivning(self, genoplivnings_skemaer):
        """
        Extract resuscitation information (genoplivningsoplysninger) from schemas and return formatted text.

        Args:
            genoplivnings_skemaer: List of resuscitation schemas

        Returns:
            str: Formatted text with resuscitation information
        """

        tekst = ""

        if len(genoplivnings_skemaer) > 0:
            tekst = "Beslutning om fravalg af genoplivning:\r\n"
            skema = self.nexus.hent_fra_reference(genoplivnings_skemaer[0])
            for item in skema["items"]:
                if item.get("value") is None or str(item.get("value")).strip() == "":
                    continue

                if item["label"] == "Beslutning om fravalg af genoplivningsforsøg":
                    item_tekst = (
                        str(item.get("value") or "").replace("\n", "\r\n").strip()
                    )
                    if item_tekst:
                        tekst += f"{item_tekst}\r\n"
                elif (
                    item["label"]
                    == "Dato for modtagelse af beslutning om fravalg af genoplivningsforsøg"
                ):
                    formatted_date = self._format_date_as_ddmmyyyy(item["value"])
                    tekst += f"Dato: {formatted_date}\r\n"

        return tekst

    def hent_cfs_oplysninger(self, cfs_skemaer):
        """
        Extract Clinical Frailty Scale (CFS) information from schemas and return formatted text.

        Args:
            cfs_skemaer: List of CFS schemas

        Returns:
            str: Formatted text with CFS information
        """
        tekst = ""

        if len(cfs_skemaer) > 0:
            skema = self.nexus.hent_fra_reference(cfs_skemaer[0])
            for item in skema.get("items", []):
                value = item.get("value")
                if isinstance(value, list) and len(value) > 0:
                    label = (item.get("label") or "").strip()
                    if label:
                        return f"\r\nCFS: {label}\r\n"

        return tekst

    def send_besked(self, besked_tekst, borger, modtager, proces_navn):
        """
        Send a MedCom message with the collected information.

        Args:
            besked_tekst: The main message text to send
            borger: The citizen/patient object
            modtager: The receiver EAN identifier
            proces_navn: The process name for tracking
        """
        endelig_tekst = "Dette er en automatisk besked med supplerende indlæggelsesoplysninger. Ved behov for kontakt til borgers hjemmepleje kan du finde et tlf. nummer i  indlæggelsesrapporten."
        endelig_tekst += f"\r\n\r\n{besked_tekst}"

        self.nexus.medcom.send_besked(
            borger=borger,
            fra="Odense Kommune - EAN:5790000121441 - Ørbækvej 100B, 5220 Odense SØ",
            til=modtager,
            tekst=endelig_tekst,
            emne="Supplerende indlæggelsesoplysninger",
        )

        self.tracker.track_task(proces_navn)
