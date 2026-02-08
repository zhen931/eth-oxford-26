// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "./IdentityRegistry.sol";
import "./AidTreasury.sol";
import "./FlareInterfaces.sol";

contract MissionControl {

    address constant FLARE_CONTRACT_REGISTRY = 0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019;
    IFlareContractRegistry public registry;

    IdentityRegistry public identityRegistry;
    AidTreasury public treasury;
    
    // Admin for LLM Oracle
    address public llmOracle;

    enum Status { PENDING, EVENT_VERIFIED, APPROVED, FULFILLED }

    struct Request {
        uint256 id;
        address requester;
        Status status;
        address assignedProvider;
        uint256 approvedCostUSD;
    }

    uint256 public requestCounter;
    mapping(uint256 => Request) public requests;
    
    event RequestCreated(uint256 indexed id, address indexed requester);
    event EventVerified(uint256 indexed id);
    event AidApproved(uint256 indexed id);
    event MissionComplete(uint256 indexed id);

    modifier onlyOracle() {
        require(msg.sender == llmOracle, "Not authorized LLM Oracle");
        _;
    }

    constructor(address _identity, address _treasury, address _llmOracle) {
        registry = IFlareContractRegistry(FLARE_CONTRACT_REGISTRY);
        identityRegistry = IdentityRegistry(_identity);
        treasury = AidTreasury(_treasury);
        llmOracle = _llmOracle;
    }

    function createRequest(string memory _gps, string memory _aidType) external {
        // ... (Same as before) ...
        requestCounter++;
        requests[requestCounter] = Request({
            id: requestCounter,
            requester: msg.sender,
            status: Status.PENDING,
            assignedProvider: address(0),
            approvedCostUSD: 0
        });
        emit RequestCreated(requestCounter, msg.sender);
    }

    // --- THE REAL FDC VERIFICATION ---
    function verifyEvent(
        uint256 _requestId, 
        bytes32[] calldata _merkleProof,
        bytes32 _merkleRoot,
        bytes32 _leaf 
    ) external {
        Request storage req = requests[_requestId];
        require(req.status == Status.PENDING, "Invalid status");

        // 1. Get the FDC Verification Contract Address dynamically
        // Note: Check the exact name in the Registry. Usually "FdcVerification" or "FdcHub"
        // For Hackathon safety, we look for "FdcVerification".
        address fdcAddr = registry.getContractAddressByName("FdcVerification");
        IFdcVerification fdc = IFdcVerification(fdcAddr);

        // 2. Verify Proof
        bool valid = fdc.verifyMerkleProof(_merkleProof, _merkleRoot, _leaf);
        require(valid, "FDC Proof Invalid");

        req.status = Status.EVENT_VERIFIED;
        emit EventVerified(_requestId);
    }
    
    // ... approveAid function (same as before) ...
    function approveAid(uint256 _requestId, address _provider, uint256 _costUSD) external onlyOracle {
        Request storage req = requests[_requestId];
        require(req.status == Status.EVENT_VERIFIED, "Event not verified");
        req.assignedProvider = _provider;
        req.approvedCostUSD = _costUSD;
        req.status = Status.APPROVED;
        emit AidApproved(_requestId);
    }

    // --- CONFIRM DELIVERY ---
    function confirmDelivery(
        uint256 _requestId,
        bytes32[] calldata _merkleProof,
        bytes32 _merkleRoot,
        bytes32 _leaf 
    ) external {
        Request storage req = requests[_requestId];
        require(req.status == Status.APPROVED, "Mission not approved");

        address fdcAddr = registry.getContractAddressByName("FdcVerification");
        bool valid = IFdcVerification(fdcAddr).verifyMerkleProof(_merkleProof, _merkleRoot, _leaf);
        require(valid, "Delivery Proof Invalid");

        req.status = Status.FULFILLED;
        treasury.processPayout(req.assignedProvider, req.approvedCostUSD);
        emit MissionComplete(_requestId);
    }
}