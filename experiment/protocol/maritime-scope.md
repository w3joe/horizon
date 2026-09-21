# Maritime scope and limitations

The study supports declared two-vessel head-on, crossing, and overtaking encounters before controlled multi-contact compositions. Collision avoidance and navigation-rule conformance are separate outputs. The [USCG-published navigation rules](https://www.navcen.uscg.gov/navigation-rules-amalgamated) require safe speed with regard to maneuverability and conditions, use of available means to assess collision risk, and action taken in ample time. They do not define one universal safe speed or passing distance. Horizon therefore uses configured, versioned margins and never reports unrestricted COLREG compliance.

The demonstrated RTA claim assumes bounded estimation error and disturbance, correct configuration, sufficient actuator authority, a functioning gate/recovery path, supported encounter behavior, and an initially recoverable state. No local controller can guarantee avoidance against unrestricted contact motion. Initially unrecoverable and out-of-domain episodes remain in the report with their own denominators.

ASTM [F3269-21](https://store.astm.org/f3269-21.html) describes an aviation/UAS runtime-assurance architectural practice. Horizon borrows the separation among complex function, monitor, recovery, and switch, but does not claim maritime certification or compliance with the complete paid standard.
