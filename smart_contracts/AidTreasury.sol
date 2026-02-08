// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import "./FlareInterfaces.sol";

interface IDexRouter {
    function swapExactTokensForTokens(
        uint amountIn,
        uint amountOutMin,
        address[] calldata path,
        address to,
        uint deadline
    ) external returns (uint[] memory amounts);
}

contract AidTreasury is Ownable {
    
    // The one constant we trust: The Coston2 Registry Address
    address constant FLARE_CONTRACT_REGISTRY = 0xaD67FE66660Fb8dFE9d6b1b4240d8650e30F6019;

    IFlareContractRegistry public registry;
    IDexRouter public dexRouter;
    
    address public missionControl;
    address public fxrpToken; 
    address public usdcToken;

    constructor(
        address _dexRouter,
        address _fxrpToken,
        address _usdcToken
    ) Ownable(msg.sender) {
        registry = IFlareContractRegistry(FLARE_CONTRACT_REGISTRY);
        dexRouter = IDexRouter(_dexRouter);
        fxrpToken = _fxrpToken;
        usdcToken = _usdcToken;
    }

    function setMissionControl(address _mc) external onlyOwner {
        missionControl = _mc;
    }

    modifier onlyMissionControl() {
        require(msg.sender == missionControl, "Caller is not Mission Control");
        _;
    }

    function processPayout(address _provider, uint256 _usdAmount) external onlyMissionControl {
        // 1. Get FTSO Registry Address dynamically
        address ftsoRegistryAddr = registry.getContractAddressByName("FtsoRegistry");
        IFtsoRegistry ftso = IFtsoRegistry(ftsoRegistryAddr);

        // 2. Get Live Price (e.g., "FXRP" or "FLR")
        (uint256 price, , uint256 decimals) = ftso.getCurrentPriceWithDecimals("FXRP");
        require(price > 0, "Invalid FTSO Price");

        // 3. Calculate Swap Amount
        // Precision handling: Ensure you match the decimals of your FXRP token
        uint256 fxrpNeeded = (_usdAmount * (10**decimals)) / price; 
        
        // 4. Swap Logic
        IERC20(fxrpToken).approve(address(dexRouter), fxrpNeeded);
        
        address[] memory path = new address[](2);
        path[0] = fxrpToken;
        path[1] = usdcToken;

        dexRouter.swapExactTokensForTokens(
            fxrpNeeded,
            0, 
            path,
            _provider, 
            block.timestamp + 300
        );
    }
}