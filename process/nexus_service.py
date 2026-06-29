from datetime import datetime, timedelta, timezone
from kmd_nexus_client import NexusClientManager
from kmd_nexus_client.tree_helpers import (
    filter_by_path,    
)
from odk_tools.tracking import Tracker
import xml.etree.ElementTree as ET


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
            
            return dt.strftime("%d/%m/%Y")
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
            ref for ref in aktiviteter if ref.get("patientActivityType") == "formData"
            and ref.get("formDefinition", {}).get("title") in ["Generelle oplysninger - V. 2025", "Helbredsoplysninger"]
            and ref.get("workflowState", {}).get("name") == "Aktivt"
        ]

        genoplivnings_skemaer = [
            ref for ref in aktiviteter if ref.get("patientActivityType") == "formData"
            and ref.get("formDefinition", {}).get("title") == "Fravalg af genoplivningsforsøg"                    
        ]

        for skema_reference in generelle_skemaer:
            skema = self.nexus.hent_fra_reference(skema_reference)
            for item in skema["items"]:
                if item["label"] in generelle_oplysninger_felter:
                    if item.get("value") is None or str(item.get("value")).strip() == "":
                        continue

                    tekst += f"{item['label']}:\n"                            
                    tekst += item['value'].replace("\n", "\r\n").strip() + "\r\n"
                    formatted_date = self._format_date_as_ddmmyyyy(skema['lastStateChange'])
                    tekst += f"Sidst opd. {formatted_date}\r\n\r\n"

        return tekst, genoplivnings_skemaer


    def hent_handlingsanvisninger(self, pathway):
        """
        Extract action guidelines (handlingsanvisninger) from schemas and return formatted text.
        
        Args:
            pathway: The patient pathway object
            
        Returns:
            str: Formatted text with action guidelines
        """
        tekst = ""
        
        referencer = self.nexus.borgere.hent_referencer(pathway)                
        
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
            skema = self.nexus.hent_fra_reference(skema_reference)
            relaterede_aktiviteter = self.nexus.nexus_client.get(skema["_links"]["relatedActivities"]["href"]).json()

            for relateret_aktivitet in relaterede_aktiviteter:
                for aktivitet in relateret_aktivitet.get("citizenActivitiesGroups", []).get("activities", []):                            
                    if aktivitet.get("activityReference", {}).get("name") in godkendte_indsatser:                                
                        for item in skema["items"]:
                            if item.get("value") is None or str(item.get("value")).strip() == "":
                                continue
                            
                            label_tekst = (item.get("label") or "").strip()
                            value_tekst = str(item.get("value") or "").replace("\n", "\r\n").strip()
                            tekst += f"\r\n{label_tekst}: {value_tekst}\r\n"

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
            skema = self.nexus.hent_fra_reference(genoplivnings_skemaer[0])
            for item in skema["items"]:
                if item.get("value") is None or str(item.get("value")).strip() == "":
                    continue
                
                if item["label"] == "Tekst":                            
                    item_tekst = str(item.get("value") or "").replace("\n", "\r\n").strip()
                    if item_tekst:
                        tekst += f"{item_tekst}\r\n"
                elif item["label"] == "Dato":                            
                    formatted_date = self._format_date_as_ddmmyyyy(item['value'])
                    tekst += f"Dato: {formatted_date}\r\n"

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
            fra=afsender,
            til=modtager,                    
            tekst=endelig_tekst,
            emne="Supplerende indlæggelsesoplysninger"
        )

        self.tracker.track_task(proces_navn)